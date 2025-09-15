import os, json, random, re, requests
from openai import OpenAI

# ===== CONFIG =====
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")  # e.g., myshop.myshopify.com
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_VERSION = "2023-10"
AUTHOR_NAME = "Brand63"
BLOG_HANDLE = "trendsetter-news"

AUTO_PUBLISH = False  # 👈 Set True to auto-publish, False = keep as draft

SESSION = requests.Session()
SESSION.headers.update({
    "X-Shopify-Access-Token": TOKEN,
    "Content-Type": "application/json",
    "Accept": "application/json"
})

# ===== SHOPIFY HELPERS =====
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

def pick_topic_and_products(products, max_count=3):
    if not products:
        raise RuntimeError("No products found with images.")
    picks = random.sample(products, k=min(max_count, len(products)))
    seed_keywords = []
    try:
        with open("keywords.csv", "r", encoding="utf-8") as f:
            for line in f:
                kw = line.strip()
                if kw:
                    seed_keywords.append(kw)
    except FileNotFoundError:
        pass
    topic_kw = random.choice(seed_keywords) if seed_keywords else picks[0]["title"]
    return topic_kw, picks

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

# ===== OPENAI HELPERS =====
def parse_title_html(raw_text: str, fallback_title: str):
    try:
        obj = json.loads(raw_text)
        if isinstance(obj, dict) and "title" in obj and "html" in obj:
            return obj["title"], obj["html"], obj.get("tags", ""), obj.get("excerpt", ""), obj.get("meta", "")
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw_text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "title" in obj and "html" in obj:
                return obj["title"], obj["html"], obj.get("tags", ""), obj.get("excerpt", ""), obj.get("meta", "")
        except Exception:
            pass
    lines = [ln.strip() for ln in raw_text.strip().splitlines() if ln.strip()]
    title = (lines[0] if lines else fallback_title)[:70]
    body_lines = lines[1:] if len(lines) > 1 else [raw_text]
    html = "<p>" + "</p><p>".join(body_lines) + "</p>"
    return title, html, "", "", ""

def openai_generate(topic, products):
    product_list_text = "\n".join([f"- {p['title']}: {p['url']}" for p in products])
    prompt = f"""
Write a Shopify blog post about: {topic}

Rules:
- Length: 600–800 words
- Tone: Friendly, helpful, and persuasive
- Include internal product links below
- Provide:
  1. "title" (60–70 chars)
  2. "html" (blog content with <h2>, <p>, and links)
  3. "tags" (comma-separated SEO keywords)
  4. "excerpt" (1–2 sentences summary)
  5. "meta" (SEO meta description)

Products to include:
{product_list_text}

Return JSON only:
{{"title": "...", "html": "...", "tags": "...", "excerpt": "...", "meta": "..." }}
"""
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-5",
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=1200,
    )
    content = resp.choices[0].message.content or ""
    return parse_title_html(content, topic)

# ===== PUBLISH TO SHOPIFY =====
def publish_article(blog_id, title, body_html, excerpt="", meta_description="", tags=None, featured_image_src=None, featured_image_alt=None):
    article = {
        "article": {
            "title": title,
            "author": AUTHOR_NAME,
            "tags": tags or "autoblog, brand63",
            "body_html": body_html,
            "published": AUTO_PUBLISH,
            "excerpt": excerpt,
            "metafields": [
                {
                    "namespace": "global",
                    "key": "description_tag",
                    "value": meta_description,
                    "type": "string"
                }
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

# ===== MAIN =====
def main():
    if not STORE or not TOKEN or not OPENAI_API_KEY:
        raise SystemExit("Missing required env vars: SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_ACCESS_TOKEN, OPENAI_API_KEY")

    blog_id = get_blog_id_by_handle(BLOG_HANDLE)
    products = get_recent_products(limit=12)
    topic, picks = pick_topic_and_products(products, max_count=3)

    title, html, tags, excerpt, meta = openai_generate(topic, picks)

    image_blocks = "\n".join(build_image_html(p) for p in picks)
    combined_html = f"""{html}
<hr/>
<section>
{image_blocks}
</section>
<p><strong>Want more?</strong> Explore our latest arrivals in the {AUTHOR_NAME} shop.</p>
"""

    featured_src = picks[0]["image"] if picks else None
    featured_alt = f"{picks[0]['title']} by {AUTHOR_NAME}" if picks else None

    result = publish_article(blog_id, title, combined_html, excerpt, meta, tags, featured_src, featured_alt)
    print("✅ Draft saved (or published if AUTO_PUBLISH=True):", result["article"]["title"])

if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()


