import os
import json
import random
import re
import requests
from openai import OpenAI

# Load environment variables
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
BLOG_HANDLE = os.getenv("BLOG_HANDLE")
AUTHOR_NAME = os.getenv("AUTHOR_NAME")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_VERSION = "2023-10"

SESSION = requests.Session()
SESSION.headers.update({
    "X-Shopify-Access-Token": TOKEN,
    "Content-Type": "application/json",
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
    # Only pull ACTIVE products
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
    return [p for p in products if p["image"]]

def pick_topic_and_products(products, max_count=3):
    if not products:
        raise RuntimeError("No products with images found. Make sure you have active products with at least one image.")
    picks = products[:max_count]
    if len(products) > max_count:
        extra = random.sample(products[max_count:], k=min(2, len(products)-max_count))
        picks.extend(extra)
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

def parse_title_html(raw_text: str, fallback_title: str):
    try:
        obj = json.loads(raw_text)
        if isinstance(obj, dict) and "title" in obj and "html" in obj:
            return obj["title"], obj["html"]
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw_text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "title" in obj and "html" in obj:
                return obj["title"], obj["html"]
        except Exception:
            pass
    lines = [ln.strip() for ln in raw_text.strip().splitlines() if ln.strip()]
    title = (lines[0] if lines else fallback_title)[:70]
    body_lines = lines[1:] if len(lines) > 1 else [raw_text]
    html = "<p>" + "</p><p>".join(body_lines) + "</p>"
    return title, html

def openai_generate(topic, products):
    product_list_text = "\n".join([f"- {p['title']}: {p['url']}" for p in products])
    prompt = f"""
You are writing a Shopify blog post to increase organic traffic and conversions.

RULES:
- Only include INTERNAL links (use the provided product URLs).
- No external links.
- 600–800 words.
- Must include meta description (150–160 chars), tags, and a 1–2 sentence excerpt.
- Format output JSON with keys: title, html, meta_description, tags, excerpt.

Products to include:
{product_list_text}
"""
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=1200,
    )
    content = resp.choices[0].message.content or ""
    return parse_title_html(content, topic)

def publish_article(blog_id, title, body_html, meta_desc, tags, excerpt, featured_image_src=None, featured_image_alt=None):
    article = {
        "article": {
            "title": title,
            "author": AUTHOR_NAME,
            "tags": tags,
            "body_html": body_html,
            "published": False,   # HIDDEN until you approve
            "excerpt": excerpt,
            "metafields": [
                {"namespace": "global", "key": "description_tag", "value": meta_desc, "type": "single_line_text_field"}
            ]
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
    if not STORE or not TOKEN or not OPENAI_API_KEY:
        raise SystemExit("Missing required env vars.")

    blog_id = get_blog_id_by_handle(BLOG_HANDLE)
    products = get_recent_products(limit=12)
    topic, picks = pick_topic_and_products(products, max_count=3)

    title, html = openai_generate(topic, picks)

    image_blocks = "\n".join(build_image_html(p) for p in picks)
    combined_html = f"""{html}
<hr/>
<section>{image_blocks}</section>
<p><strong>Want more?</strong> Explore our latest arrivals in the {AUTHOR_NAME} shop.</p>
"""

    featured_src = picks[0]["image"] if picks else None
    featured_alt = f"{picks[0]['title']} by {AUTHOR_NAME}" if picks else None

    # Placeholder meta/excerpt until AI JSON parsing is improved
    meta_desc = f"Discover {topic} at {AUTHOR_NAME}. Shop trending products now."
    excerpt = f"A quick look at {topic} featuring our latest picks."

    result = publish_article(blog_id, title, combined_html, meta_desc, "autoblog, seo", excerpt, featured_src, featured_alt)
    print("✅ Published:", result["article"]["title"])

if __name__ == "__main__":
    main()


