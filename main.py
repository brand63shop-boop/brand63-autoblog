import os, json, random, requests
from openai import OpenAI

# ===== CONFIG =====
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_VERSION = "2023-10"
AUTHOR_NAME = "Brand63"
BLOG_HANDLE = "trendsetter-news"

AUTO_PUBLISH = False  # keep posts hidden (drafts) until approved

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
    if not r.ok:
        print("❌ Shopify POST Error:", r.status_code)
        print("Response Text:", r.text)
    r.raise_for_status()
    return r.json()

def get_blog_id_by_handle(handle: str):
    """Get blog ID by handle or create it if missing."""
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

def get_all_collections(limit=250):
    """Fetch all collections from Shopify."""
    data = shopify_get("custom_collections.json", params={"limit": limit})
    collections = []
    for c in data.get("custom_collections", []):
        collections.append({
            "id": c["id"],
            "title": c["title"],
            "handle": c["handle"]
        })
    return collections

def get_products_from_collection(collection_id, limit=20):
    """Fetch products inside a collection."""
    data = shopify_get(f"collections/{collection_id}/products.json", params={"limit": limit})
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

# ===== TOPIC + PRODUCT PICKER =====
def pick_topic_and_products():
    """Pick one random collection and sample products from it."""
    collections = get_all_collections()
    if not collections:
        raise RuntimeError("⚠️ No collections found in Shopify.")

    chosen = random.choice(collections)
    products = get_products_from_collection(chosen["id"], limit=20)

    if not products:
        raise RuntimeError(f"⚠️ No products found in collection: {chosen['title']}")

    picks = random.sample(products, k=min(3, len(products)))
    return chosen["title"], picks

# ===== IMAGE BUILDER =====
def build_image_html(p):
    alt = f"{p['title']} by {AUTHOR_NAME}"
    html = (
        f"<figure>"
        f"<a href='{p['url']}' target='_self' rel='noopener'>"
        f"<img src='{p['image']}' alt='{alt}' loading='lazy' />"
        f"</a>"
        f"<figcaption><a href='{p['url']}' target='_self'>Shop {p['title']}</a></figcaption>"
        f"</figure>"
    )
    return html

# ===== AI BLOG GENERATION =====
def openai_generate(topic, products):
    """Ask OpenAI to generate a blog post about one collection."""
    product_list_text = "\n".join([f"- {p['title']} ({p['url']})" for p in products])
    client = OpenAI(api_key=OPENAI_API_KEY)

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a JSON-only Shopify blog generator. Output must be valid JSON."},
            {"role": "user", "content": f"""
Write a Shopify blog post (600–800 words) about the collection: **{topic}**.
Weave in these products naturally:
{product_list_text}

Rules:
- Use <h2>, <h3>, <p> for formatting.
- Focus only on products from this collection.
- Return JSON with keys: title, html, tags, excerpt, meta_description.
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
                        "meta_description": {"type": "string"}
                    },
                    "required": ["title", "html", "tags", "excerpt", "meta_description"]
                }
            }
        },
        max_completion_tokens=1500,
    )

    obj = json.loads(resp.choices[0].message.content)

    # fallback excerpt if missing
    excerpt = (obj.get("excerpt") or "").strip()
    if not excerpt:
        excerpt = " ".join(obj["html"].split()[:30]) + "..."

    return (
        obj["title"],
        obj["html"],
        obj["tags"],
        excerpt,
        obj["meta_description"],
    )

# ===== PUBLISH BLOG =====
def publish_article(blog_id, title, body_html, meta_desc, tags, excerpt,
                    featured_image_src=None, featured_image_alt=None):
    """Publish or save a new article safely with clean tags & excerpt."""
    meta_desc = (meta_desc or "").strip()[:300]
    excerpt = (excerpt or "").strip()[:250]
    if not excerpt:
        excerpt = "Read the latest updates and fashion highlights from Brand63."

    cleaned_tags = ",".join(
        [t.strip().replace("#", "").replace("|", "") + " blog"
         for t in tags.split(",") if t.strip()]
    )

    article = {
        "article": {
            "title": title[:250],
            "author": AUTHOR_NAME,
            "tags": cleaned_tags,
            "body_html": body_html,
            "published": AUTO_PUBLISH,
            "excerpt": excerpt,
            "excerpt_html": f"<p>{excerpt}</p>"
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
    """Main script to generate and upload one blog post."""
    if not STORE or not TOKEN or not OPENAI_API_KEY:
        raise SystemExit("Missing env vars. Check SHOPIFY and OPENAI keys.")

    blog_id = get_blog_id_by_handle(BLOG_HANDLE)
    topic, picks = pick_topic_and_products()

    title, html, tags, excerpt, meta = openai_generate(topic, picks)

    image_blocks = "\n".join(build_image_html(p) for p in picks)
    combined_html = f"{html}\n<hr/>\n<section>{image_blocks}</section>"

    featured_src = picks[0]["image"] if picks else None
    featured_alt = picks[0]["title"] if picks else None

    try:
        result = publish_article(blog_id, title, combined_html, meta, tags, excerpt, featured_src, featured_alt)
        print("\n✅ Draft saved successfully:", result["article"]["title"])
        print("📜 Shopify Response:")
        print(json.dumps(result, indent=2))
    except requests.exceptions.HTTPError as e:
        print("\n❌ Shopify rejected the blog post.")
        print("Response code:", e.response.status_code)
        print("Full message:", e.response.text)

if __name__ == "__main__":
    main()


