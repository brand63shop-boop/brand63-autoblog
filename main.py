import os, json, random, requests, re
from openai import OpenAI

# ===== CONFIG =====
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_VERSION = "2023-10"
AUTHOR_NAME = "Brand63"
BLOG_HANDLE = "trendsetter-news"

AUTO_PUBLISH = False  # drafts until approved

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

def get_recent_products(limit=250):
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
        })
    return [p for p in products if p["image"]]

def pick_topic_and_products(products, max_count=3):
    if not products:
        raise RuntimeError("No active products with images found.")
    return random.sample(products, k=min(max_count, len(products)))

def build_image_html(p, keyword=""):
    alt = f"{keyword} – {p['title']} | {AUTHOR_NAME}"
    return f"""
<figure>
  <a href="{p['url']}" target="_self" rel="noopener">
    <img src="{p['image']}" alt="{alt}" loading="lazy" />
  </a>
  <figcaption><a href="{p['url']}" target="_self">Shop {p['title']}</a></figcaption>
</figure>
""".strip()

# ===== KEYWORDS =====
def load_keywords():
    try:
        with open("keywords.csv", "r", encoding="utf-8") as f:
            kws = [line.strip() for line in f if line.strip()]
        return kws
    except FileNotFoundError:
        return ["streetwear trends", "anime t-shirts", "hoodie styling tips"]

# ===== AI BLOG GENERATION =====
def openai_generate(keyword, products):
    product_list_text = "\n".join([f"- {p['title']} ({p['url']})" for p in products])
    client = OpenAI(api_key=OPENAI_API_KEY)

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a JSON-only Shopify blog generator. Output must be valid JSON."},
            {"role": "user", "content": f"""
Write a Shopify blog post (1000–1400 words) for the keyword: "{keyword}".

Products to feature:
{product_list_text}

Rules:
- Use <h2>, <h3>, <p> for formatting.
- Include a short intro, body sections, and strong conclusion.
- Add an FAQ section with at least 3 questions/answers.
- Only use internal product links.
- Include these outputs in JSON with keys: 
  title, html, tags, excerpt, meta_description, faq.
"""}
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "blog_post",
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "html": {"type": "string"},
                        "tags": {"type": "string"},
                        "excerpt": {"type": "string"},
                        "meta_description": {"type": "string"},
                        "faq": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "q": {"type": "string"},
                                    "a": {"type": "string"}
                                },
                                "required": ["q","a"]
                            }
                        }
                    },
                    "required": ["title", "html", "tags", "excerpt", "meta_description", "faq"]
                }
            }
        },
        max_completion_tokens=2000,
    )

    obj = json.loads(resp.choices[0].message.content)
    return obj

# ===== SCHEMA BUILDER =====
def build_schema(obj, keyword):
    faq_entries = [{"@type": "Question", "name": qa["q"], "acceptedAnswer": {"@type": "Answer", "text": qa["a"]}} for qa in obj.get("faq",[])]
    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": obj["title"],
        "author": AUTHOR_NAME,
        "about": keyword,
        "mainEntity": faq_entries
    }
    return f'<script type="application/ld+json">{json.dumps(schema)}</script>'

# ===== PUBLISH BLOG =====
def publish_article(blog_id, title, body_html, meta_desc, tags, excerpt, featured_image_src=None, featured_image_alt=None):
    article = {
        "article": {
            "title": title,
            "author": AUTHOR_NAME,
            "tags": ",".join(tags.split(",")[:6]),  # cap to 6 tags
            "body_html": body_html,
            "published": AUTO_PUBLISH,
            "summary_html": excerpt,
            "metafields": [
                {
                    "namespace": "global",
                    "key": "description_tag",
                    "value": meta_desc,
                    "type": "single_line_text_field"
                }
            ]
        }
    }
    if featured_image_src:
        article["article"]["image"] = {
            "src": featured_image_src,
            "alt": featured_image_alt or title
        }
    return shopify_post(f"blogs/{blog_id}/articles.json", article)

# ===== MAIN =====
def main():
    if not STORE or not TOKEN or not OPENAI_API_KEY:
        raise SystemExit("Missing env vars.")

    blog_id = get_blog_id_by_handle(BLOG_HANDLE)
    products = get_recent_products(limit=250)
    picks = pick_topic_and_products(products, max_count=3)

    keywords = load_keywords()
    keyword = random.choice(keywords)

    obj = openai_generate(keyword, picks)

    # add schema
    schema_block = build_schema(obj, keyword)

    image_blocks = "\n".join(build_image_html(p, keyword) for p in picks)
    combined_html = f"""{obj['html']}
<hr/>
<section>{image_blocks}</section>
{schema_block}
"""

    featured_src = picks[0]["image"] if picks else None
    featured_alt = picks[0]["title"] if picks else None

    result = publish_article(blog_id, obj["title"], combined_html, obj["meta_description"], obj["tags"], obj["excerpt"], featured_src, featured_alt)
    print("✅ Draft saved:", result["article"]["title"])

if __name__ == "__main__":
    main()


