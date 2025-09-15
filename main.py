import os
import json
import random
import requests

# =============================
# Config & Helpers
# =============================

STORE = os.environ.get("SHOPIFY_STORE_DOMAIN")  # e.g., brand63.myshopify.com
TOKEN = os.environ.get("SHOPIFY_ADMIN_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BLOG_HANDLE = os.environ.get("BLOG_HANDLE", "news")
AUTHOR_NAME = os.environ.get("AUTHOR_NAME", "Brand63")
API_VERSION = "2024-07"  # Shopify Admin API version

SESSION = requests.Session()
SESSION.headers.update({
    "X-Shopify-Access-Token": TOKEN,
    "Content-Type": "application/json"
})

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

def get_blog_id_by_handle(handle: str):
    # Find blog by handle; if not found, default to first blog; if none, create one.
    data = shopify_get("blogs.json")
    blogs = data.get("blogs", [])
    for b in blogs:
        if b.get("handle") == handle:
            return b.get("id")
    if blogs:
        return blogs[0].get("id")
    payload = {"blog": {"title": handle.capitalize(), "handle": handle}}
    created = shopify_post("blogs.json", payload)
    return created["blog"]["id"]

def get_recent_products(limit=12):
    # Get most recent active products (needs images for the post)
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
            "vendor": p.get("vendor", ""),
            "body_html": p.get("body_html", "")
        })
    return [p for p in products if p["image"]]  # ensure we have an image

def pick_topic_and_products(products, max_count=3):
    if not products:
        raise RuntimeError("No products with images found. Make sure you have active products with at least one image.")
    picks = products[:max_count]
    if len(products) > max_count:
        extra = random.sample(products[max_count:], k=min(2, len(products)-max_count))
        picks.extend(extra)
    # Load seed keywords if present
    seed_keywords = []
    try:
        with open("keywords.csv", "r", encoding="utf-8") as f:
            for line in f:
                kw = line.strip()
                if kw:
                    seed_keywords.append(kw)
    except FileNotFoundError:
        pass
    topic_kw = random.choice(seed_keywords) if seed_keywords else products[0]["title"]
    return topic_kw, picks[:max_count]

def build_image_html(p):
    alt = f"{p['title']} by {AUTHOR_NAME}"
    return """
<figure>
  <a href="{url}" target="_self" rel="noopener">
    <img src="{img}" alt="{alt}" loading="lazy" />
  </a>
  <figcaption><a href="{url}" target="_self">Shop {title}</a></figcaption>
</figure>
""".format(url=p['url'], img=p['image'], alt=alt, title=p['title']).strip()

def openai_generate(topic, products):
 def parse_title_html(raw_text: str, fallback_title: str):
    # 1) try direct JSON
    try:
        obj = json.loads(raw_text)
        if isinstance(obj, dict) and "title" in obj and "html" in obj:
            return obj["title"], obj["html"]
    except Exception:
        pass

    # 2) try to extract a JSON block from within the text
    m = re.search(r"\{[\s\S]*\}", raw_text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "title" in obj and "html" in obj:
                return obj["title"], obj["html"]
        except Exception:
            pass

    # 3) last-resort fallback: treat first line as title, rest as HTML paragraphs
    lines = [ln.strip() for ln in raw_text.strip().splitlines() if ln.strip()]
    title = (lines[0] if lines else fallback_title)[:70]
    body_lines = lines[1:] if len(lines) > 1 else [raw_text]
    html = "<p>" + "</p><p>".join(body_lines) + "</p>"
    return title, html

def openai_generate(topic, products):
    product_list_text = "\n".join([f"- {p['title']}: {p['url']}" for p in products])
    prompt = f"""
You are writing a Shopify blog post to increase organic traffic, search authority, reader interest, and conversions.
RULES:
- Only include INTERNAL links (use the provided product URLs).
- No external links. No placeholders.
- 600–800 words. Use clear headings (H2/H3).
- Friendly, helpful, non-spammy tone.
- Include a short intro, useful sections, and a strong CTA to shop.
- Weave in these products naturally:
{product_list_text}

Return strictly JSON with two keys only:
{{"title": "... (60-70 chars)", "html": "<h2>...</h2><p>...</p>"}}
No prose outside JSON.
"""
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    # First attempt
    resp = client.chat.completions.create(
        model="gpt-5",
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=1100,
    )
    content = resp.choices[0].message.content or ""

    title, html = _parse_title_html(content, topic)
    if title and html:
        return title, html

    # If the model still didn't comply, ask it to convert response to JSON only
    fix_prompt = f"Convert the following to valid JSON with only keys 'title' and 'html'. Do NOT add any other text:\n\n{content}"
    resp2 = client.chat.completions.create(
        model="gpt-5",
        messages=[{"role": "user", "content": fix_prompt}],
        max_completion_tokens=400,
    )
    content2 = resp2.choices[0].message.content or ""
    return _parse_title_html(content2, topic)


def publish_article(blog_id, title, body_html, featured_image_src=None, featured_image_alt=None):
    article = {
        "article": {
            "title": title,
            "author": AUTHOR_NAME,
            "tags": "autoblog, seo, brand63",
            "body_html": body_html,
            "published": True
        }
    }
    if featured_image_src:
        article["article"]["image"] = {
            "src": featured_image_src,
            "alt": featured_image_alt or title
        }
    created = shopify_post(f"blogs/{blog_id}/articles.json", article)
    return created

def main():
    # Basic env checks
    if not STORE or not TOKEN or not OPENAI_API_KEY:
        raise SystemExit("Missing required env vars: SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_ACCESS_TOKEN, OPENAI_API_KEY")

    # Blog & products
    blog_id = get_blog_id_by_handle(BLOG_HANDLE)
    products = get_recent_products(limit=12)
    topic, picks = pick_topic_and_products(products, max_count=3)

    # Generate article content
    title, html = openai_generate(topic, picks)

    # Add image blocks
    image_blocks = "\n".join(build_image_html(p) for p in picks)
    combined_html = '''{html}
<hr/>
<section>
{images}
</section>
<p><strong>Want more?</strong> Explore our latest arrivals and limited drops in the {author} shop.</p>
'''.format(html=html, images=image_blocks, author=AUTHOR_NAME)

    # Featured image = first product image
    featured_src = picks[0]["image"] if picks else None
    featured_alt = f"{picks[0]['title']} by {AUTHOR_NAME}" if picks else None

    # Publish
    result = publish_article(blog_id, title, combined_html, featured_src, featured_alt)
    print("✅ Published:", result["article"]["title"])
    print("🆔 Article ID:", result["article"]["id"])
    print("🔗 Handle:   ", result["article"]["handle"])
    print("📅 Published:", result["article"]["published_at"])

if __name__ == "__main__":
    main()

