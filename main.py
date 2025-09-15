import os, json, random, re, requests
from openai import OpenAI

# ----------------------------
# ENV VARS
# ----------------------------
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_VERSION = "2023-07"
BLOG_HANDLE = "trendsetter-news"
AUTHOR_NAME = "Brand63"

SESSION = requests.Session()
SESSION.headers.update({
    "X-Shopify-Access-Token": TOKEN,
    "Content-Type": "application/json",
})

# ----------------------------
# SHOPIFY HELPERS
# ----------------------------
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

# ----------------------------
# PRODUCT SELECTION (ROTATES)
# ----------------------------
def pick_topic_and_products(products, max_count=3):
    if not products:
        raise RuntimeError("No products found with images.")
    picks = random.sample(products, k=min(max_count, len(products)))

    seed_keywords = []
    try:
        with open("keywords.csv", "r", encoding="utf-8") as f:
            seed_keywords = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        pass

    topic = random.choice(seed_keywords) if seed_keywords else random.choice(products)["title"]
    return topic, picks

# ----------------------------
# IMAGE HTML
# ----------------------------
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

# ----------------------------
# AI PARSING
# ----------------------------
def parse_ai_json(raw_text: str, fallback_title: str):
    try:
        obj = json.loads(raw_text)
        if all(k in obj for k in ["title", "html"]):
            return (
                obj["title"],
                obj["html"],
                obj.get("excerpt", ""),
                obj.get("meta_description", ""),
                obj.get("tags", []),
            )
    except Exception:
        pass

    # fallback: minimal blog
    lines = [ln.strip() for ln in raw_text.strip().splitlines() if ln.strip()]
    title = (lines[0] if lines else fallback_title)[:70]
    html = "<p>" + "</p><p>".join(lines[1:] if len(lines) > 1 else [raw_text]) + "</p>"
    return title, html, "", "", ["autoblog"]

# ----------------------------
# AI BLOG GENERATION
# ----------------------------
def openai_generate(topic, products):
    product_list_text = "\n".join([f"- {p['title']}: {p['url']}" for p in products])
    prompt = f"""
Write a Shopify blog post that increases SEO, traffic, and conversions.

Requirements:
- Word count: 600–800
- Use clear <h2>/<h3> headings
- Friendly, helpful tone (not spammy)
- Naturally recommend these products (internal links only):
{product_list_text}

Return ONLY valid JSON with these keys:
{{
  "title": "SEO-friendly title (60–70 chars)",
  "html": "<h2>...</h2><p>Full article in HTML here</p>",
  "excerpt": "1–2 sentence preview (20–30 words)",
  "meta_description": "SEO meta description (120–160 chars)",
  "tags": ["keyword1", "keyword2", "keyword3"]
}}
"""

    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-5",
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=1100,
    )
    content = (resp.choices[0].message.content or "").strip()

    return parse_ai_json(content, topic)

# ----------------------------
# PUBLISH BLOG
# ----------------------------
def publish_article(blog_id, title, body_html, excerpt="", meta_description="", tags=None, featured_image_src=None, featured_image_alt=None):
    article = {
        "article": {
            "title": title,
            "author": AUTHOR_NAME,
            "tags": ", ".join(tags or ["autoblog"]),
            "body_html": body_html,
            "published": True,
            "excerpt": excerpt,
            "metafields": [
                {"namespace": "global", "key": "description_tag", "value": meta_description, "type": "string"}
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

# ----------------------------
# MAIN
# ----------------------------
def main():
    if not STORE or not TOKEN or not OPENAI_API_KEY:
        raise SystemExit("Missing required env vars: SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_ACCESS_TOKEN, OPENAI_API_KEY")

    blog_id = get_blog_id_by_handle(BLOG_HANDLE)
    products = get_recent_products(limit=12)
    topic, picks = pick_topic_and_products(products, max_count=3)

    title, html, excerpt, meta_description, tags = openai_generate(topic, picks)

    # Debug preview
    print("🤖 Raw AI Output Preview:")
    print(html[:500])

    image_blocks = "\n".join(build_image_html(p) for p in picks)
    combined_html = f"""{html}
<hr/>
<section>
{image_blocks}
</section>
<p><strong>Want more?</strong> Explore our latest arrivals and limited drops in the {AUTHOR_NAME} shop.</p>
"""

    featured_src = picks[0]["image"] if picks else None
    featured_alt = f"{picks[0]['title']} by {AUTHOR_NAME}" if picks else None

    result = publish_article(blog_id, title, combined_html, excerpt, meta_description, tags, featured_src, featured_alt)

    print("✅ Published:", result["article"]["title"])
    print("🆔 Article ID:", result["article"]["id"])
    print("🔗 Handle:   ", result["article"]["handle"])
    print("📅 Published:", result["article"]["published_at"])

if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()


