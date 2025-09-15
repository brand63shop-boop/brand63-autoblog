import os
import json
import random
import re
import requests
from openai import OpenAI

# ====== Config ======
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")           # e.g. brand63.myshopify.com
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
BLOG_HANDLE = os.getenv("BLOG_HANDLE", "news")      # your blog handle, e.g. trendsetter-news
AUTHOR_NAME = os.getenv("AUTHOR_NAME", "Brand63")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_VERSION = "2024-07"

SESSION = requests.Session()
SESSION.headers.update({
    "X-Shopify-Access-Token": TOKEN,
    "Content-Type": "application/json"
})

# ====== Shopify helpers ======
def shopify_get(path, params=None):
    url = f"https://{STORE}/admin/api/{API_VERSION}/{path}"
    r = SESSION.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def shopify_post(path, payload):
    url = f"https://{STORE}/admin/api/{API_VERSION}/{path}"
    r = SESSION.post(url, data=json.dumps(payload), timeout=120)
    r.raise_for_status()
    return r.json()

def set_article_meta_description(article_id, description):
    """Create/overwrite the SEO meta description as a metafield."""
    try:
        payload = {
            "metafield": {
                "namespace": "global",
                "key": "description_tag",
                "type": "single_line_text_field",
                "value": description[:320],
                "owner_id": article_id,
                "owner_resource": "article"
            }
        }
        shopify_post("metafields.json", payload)
    except Exception as e:
        print("⚠️ Could not set meta description:", e)

def get_blog_id_by_handle(handle: str):
    data = shopify_get("blogs.json")
    blogs = data.get("blogs", [])
    for b in blogs:
        if b.get("handle") == handle:
            return b.get("id")
    if blogs:
        return blogs[0].get("id")
    # Create blog if not found
    created = shopify_post("blogs.json", {"blog": {"title": handle.capitalize(), "handle": handle}})
    return created["blog"]["id"]

def get_recent_products(limit=12):
    params = {"limit": limit, "order": "created_at desc", "status": "active"}
    data = shopify_get("products.json", params=params)
    products = []
    for p in data.get("products", []):
        imgs = p.get("images", [])
        first_img = imgs[0]["src"] if imgs else None
        products.append({
            "id": p["id"],
            "title": p["title"],
            "handle": p["handle"],
            "url": f"https://{STORE.replace('.myshopify.com','')}.myshopify.com/products/{p['handle']}",
            "image": first_img,
            "tags": p.get("tags", ""),
            "vendor": p.get("vendor", "")
        })
    return [p for p in products if p["image"]]

def pick_topic_and_products(products, max_count=3):
    if not products:
        raise RuntimeError("No products with images found. Add active products with at least one image.")
    picks = products[:max_count]
    if len(products) > max_count:
        picks.extend(random.sample(products[max_count:], k=min(2, len(products)-max_count)))
    # Load optional seed keywords
    seed_keywords = []
    try:
        with open("keywords.csv", "r", encoding="utf-8") as f:
            for line in f:
                kw = line.strip()
                if kw:
                    seed_keywords.append(kw)
    except FileNotFoundError:
        pass
    topic = random.choice(seed_keywords) if seed_keywords else products[0]["title"]
    return topic, picks[:max_count]

def build_image_html(p):
    alt = f"{p['title']} by {AUTHOR_NAME}"
    return f"""
<figure>
  <a href="{p['url']}" target="_self" rel="noopener">
    <img src="{p['image']}" alt="{alt}" loading="lazy" />
  </a>
  <figcaption><a href="{p['url']}" target="_self">Shop {p['title']}</a></figcaption>
</figure>
""".strip()

# ====== OpenAI helpers ======
def parse_ai_json(raw_text: str, fallback_title: str):
    """Parse model output into (title, html, excerpt, meta_description, tags_list). Never crash."""
    obj = None
    # 1) try direct JSON
    try:
        obj = json.loads(raw_text)
    except Exception:
        # 2) try JSON block inside text
        m = re.search(r"\{[\s\S]*\}", raw_text)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        obj = {}

    title = obj.get("title") or fallback_title[:70]
    html_body = obj.get("html") or f"<p>{fallback_title}</p>"
    excerpt = obj.get("excerpt")
    meta_desc = obj.get("meta_description")
    tags = obj.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    # Build excerpt if missing (strip tags)
    if not excerpt:
        text_only = re.sub(r"<[^>]+>", " ", html_body)
        words = text_only.split()
        excerpt = " ".join(words[:30])

    # Build meta description if missing
    if not meta_desc:
        meta_desc = excerpt

    # Ensure reasonable defaults
    if not tags:
        tags = [fallback_title, "trending", "gift ideas", "brand63"]

    return title, html_body, excerpt, meta_desc, tags

def openai_generate(topic, products):
    product_list_text = "\n".join([f"- {p['title']}: {p['url']}" for p in products])
    prompt = f"""
You are writing a Shopify blog post to increase organic traffic, search authority, reader interest, and conversions.

RULES:
- Use ONLY the internal product URLs provided. No external links.
- 600–800 words.
- Use clear <h2>/<h3> headings and helpful paragraphs.
- Helpful, friendly tone. No fluff.
- Naturally recommend these products:
{product_list_text}

Return ONLY valid JSON with EXACTLY these keys:
{{
  "title": "60–70 character SEO title",
  "html": "<h2>...</h2><p>...</p> (full article body, HTML only)",
  "excerpt": "20–30 word summary for previews",
  "meta_description": "120–160 character SEO description",
  "tags": ["3 to 6 short tags"]
}}
Do not add any text outside JSON.
"""
    client = OpenAI(api_key=OPENAI_API_KEY)

    # First attempt
    resp = client.chat.completions.create(
        model="gpt-5",
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=1100,
    )
    content = (resp.choices[0].message.content or "").strip()
    title, html_body, excerpt, meta_desc, tags = parse_ai_json(content, topic)
    if title and html_body:
        return title, html_body, excerpt, meta_desc, tags

    # Second attempt: ask to convert to JSON only
    fix_prompt = f"Convert to valid JSON with keys title, html, excerpt, meta_description, tags only:\n\n{content}"
    resp2 = client.chat.completions.create(
        model="gpt-5",
        messages=[{"role": "user", "content": fix_prompt}],
        max_completion_tokens=700,
    )
    content2 = (resp2.choices[0].message.content or "").strip()
    return parse_ai_json(content2, topic)

# ====== Publish ======
def publish_article(blog_id, title, body_html, featured_image_src=None,
                    featured_image_alt=None, excerpt=None, tags=None, meta_description=None):
    # Shopify expects tags as a comma-separated string
    tag_string = ", ".join(tags or [])

    article = {
        "article": {
            "title": title,
            "author": AUTHOR_NAME,
            "tags": tag_string,
            "body_html": body_html,
            "summary_html": excerpt or "",   # excerpt/preview
            "published": True
        }
    }

    if featured_image_src:
        article["article"]["image"] = {"src": featured_image_src, "alt": featured_image_alt or title}

    created = shopify_post(f"blogs/{blog_id}/articles.json", article)

    # Try to set meta description via metafield (non-fatal if it fails)
    if meta_description:
        try:
            set_article_meta_description(created["article"]["id"], meta_description)
        except Exception as e:
            print("⚠️ Could not attach meta description metafield:", e)

    return created

# ====== Main ======
def main():
    if not STORE or not TOKEN or not OPENAI_API_KEY:
        raise SystemExit("❌ Missing env vars: SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_ACCESS_TOKEN, OPENAI_API_KEY")

    blog_id = get_blog_id_by_handle(BLOG_HANDLE)
    products = get_recent_products(limit=12)
    topic, picks = pick_topic_and_products(products, max_count=3)

    # Generate AI article
    title, html_body, excerpt, meta_desc, tags = openai_generate(topic, picks)

    # Append product section
    image_blocks = "\n".join(build_image_html(p) for p in picks)
    combined_html = f"""{html_body}
<hr/>
<h2>Shop These Featured Picks</h2>
{image_blocks}
"""

    # Featured image = first product image (simple + reliable)
    featured_src = picks[0]["image"] if picks else None
    featured_alt = f"{picks[0]['title']} by {AUTHOR_NAME}" if picks else None

    result = publish_article(
        blog_id, title, combined_html,
        featured_src, featured_alt,
        excerpt=excerpt, tags=tags, meta_description=meta_desc
    )

    print("✅ Published:", result["article"]["title"])
    print("🆔 Article ID:", result["article"]["id"])
    print("🔗 Handle:   ", result["article"]["handle"])
    print("📅 Published:", result["article"]["published_at"])

if __name__ == "__main__":
    main()


