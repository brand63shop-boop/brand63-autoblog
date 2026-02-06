import os, json, random, re
from datetime import datetime
import requests
from openai import OpenAI

# =========================
# CONFIG
# =========================
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")  # like brand63.myshopify.com
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

API_VERSION = "2023-10"
AUTHOR_NAME = os.getenv("AUTHOR_NAME", "Brand63")
BLOG_HANDLE = os.getenv("BLOG_HANDLE", "trendsetter-news")

# Keep posts hidden (draft) until you approve
AUTO_PUBLISH = False

# Used for product links in the blog
PUBLIC_DOMAIN = os.getenv("PUBLIC_DOMAIN", "www.brand63.com")

# How many products to pull from each collection (bigger = more chance to include truly new items)
PER_COLLECTION_PULL = 200

# How many newest products (combined) we consider for selection
NEWEST_POOL_SIZE = 300

# Strongly prefer the newest items by sampling only from the top N newest products
FRESH_SAMPLE_WINDOW = 60

# How many products to feature in each blog post
PICKS_PER_POST = 3

# ✅ Edit these handles to match YOUR store’s collection handles exactly.
# You can include as many as you want.
PRIORITY_COLLECTION_HANDLES = [
    "new-arrivals-customized-apparel-and-gifts",
    "mens-clothing-brands",
    "womens-clothing-sale",
    "anime-apparel-gifts-anime-and-manga-japan-sale",
    "nj-wear-2008",
    "faith-based-clothing-and-gifts",
    "specialty-coffee-mugs",
    "boys-apparel-and-gift-sale",
    "girls-clothing-sale"',
    "custom-infant-clothing-accessories",
    "hoodie-sale-best-deals-fave-styles",
    "purses-bags-and-totes",
    "clearance-sale-shop-up-to-90-off",
    "bts",
    "create-your-own-featured",
    "hungry-by-design",
    "black-girl-mug-collection",
]

# =========================
# HTTP SESSION
# =========================
SESSION = requests.Session()
SESSION.headers.update({
    "X-Shopify-Access-Token": TOKEN,
    "Content-Type": "application/json",
    "Accept": "application/json"
})

# =========================
# SHOPIFY HELPERS
# =========================
def shopify_get(path, params=None):
    url = f"https://{STORE}/admin/api/{API_VERSION}/{path}"
    r = SESSION.get(url, params=params, timeout=60)
    if not r.ok:
        print("❌ Shopify GET Error:", r.status_code)
        print("Response Text:", r.text)
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
    data = shopify_get("blogs.json")
    blogs = data.get("blogs", [])
    for b in blogs:
        if b.get("handle") == handle:
            return b.get("id")
    # If blog doesn't exist, create it
    payload = {"blog": {"title": handle.replace("-", " ").title(), "handle": handle}}
    created = shopify_post("blogs.json", payload)
    return created["blog"]["id"]

def get_all_collections(limit=250):
    """Fetch both custom + smart collections so handles can be found reliably."""
    collections = []

    # Custom collections
    try:
        data = shopify_get("custom_collections.json", params={"limit": limit})
        for c in data.get("custom_collections", []):
            collections.append({"id": c["id"], "title": c["title"], "handle": c["handle"]})
    except Exception:
        pass

    # Smart collections
    try:
        data = shopify_get("smart_collections.json", params={"limit": limit})
        for c in data.get("smart_collections", []):
            collections.append({"id": c["id"], "title": c["title"], "handle": c["handle"]})
    except Exception:
        pass

    # Deduplicate by handle
    seen = set()
    unique = []
    for c in collections:
        h = (c.get("handle") or "").strip().lower()
        if h and h not in seen:
            seen.add(h)
            unique.append(c)
    return unique

def get_collection_by_handle(handle: str):
    handle = (handle or "").strip().lower()
    if not handle:
        return None
    for c in get_all_collections(limit=250):
        if (c.get("handle") or "").strip().lower() == handle:
            return c
    return None

def parse_created_at(p):
    # Shopify created_at is ISO-8601; lex sort works, but we'll parse to be safe
    s = p.get("created_at") or ""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.min

def get_products_from_collection(collection_id, limit=200):
    """Fetch products inside a collection, keep only active with at least 1 image."""
    # Shopify endpoint returns products but not always "status"; we keep image-required and assume collection is online-facing.
    data = shopify_get(f"collections/{collection_id}/products.json", params={"limit": min(250, limit)})
    products = []
    for p in data.get("products", []):
        imgs = p.get("images", [])
        first_img = imgs[0]["src"] if imgs else None
        if not first_img:
            continue

        # Build public product URL
        handle = p.get("handle") or ""
        products.append({
            "id": p.get("id"),
            "title": p.get("title", ""),
            "handle": handle,
            "created_at": p.get("created_at", ""),
            "url": f"https://{PUBLIC_DOMAIN}/products/{handle}",
            "image": first_img,
            "tags": p.get("tags", "") or "",
            "vendor": p.get("vendor", "") or ""
        })
    return products

def get_newest_products_from_priority_collections():
    """Pull from your priority collections, combine, sort by newest, and return a newest pool."""
    combined = []
    found_any_collection = False

    for handle in PRIORITY_COLLECTION_HANDLES:
        c = get_collection_by_handle(handle)
        if not c:
            print(f"⚠️ Collection handle not found in Shopify: {handle}")
            continue

        found_any_collection = True
        items = get_products_from_collection(c["id"], limit=PER_COLLECTION_PULL)
        if items:
            combined.extend(items)
        else:
            print(f"⚠️ No products (with images) found in collection: {handle}")

    if not found_any_collection:
        raise RuntimeError("No priority collections were found. Update PRIORITY_COLLECTION_HANDLES to match your store.")

    # Deduplicate products by id
    uniq = {}
    for p in combined:
        pid = p.get("id")
        if pid and pid not in uniq:
            uniq[pid] = p

    combined = list(uniq.values())
    combined.sort(key=parse_created_at, reverse=True)

    # Return the newest pool
    return combined[:min(NEWEST_POOL_SIZE, len(combined))]

def choose_fresh_products(products, k=3):
    if not products:
        raise RuntimeError("No usable products found in your selected collections.")
    window = products[:min(FRESH_SAMPLE_WINDOW, len(products))]
    return random.sample(window, k=min(k, len(window)))

def build_image_html(p):
    alt = f"{p['title']} by {AUTHOR_NAME}"
    return (
        "<figure>"
        f"<a href='{p['url']}' target='_self' rel='noopener'>"
        f"<img src='{p['image']}' alt='{alt}' loading='lazy' />"
        "</a>"
        f"<figcaption><a href='{p['url']}' target='_self'>Shop {p['title']}</a></figcaption>"
        "</figure>"
    )

def strip_tags(html):
    return re.sub(r"<[^>]*>", " ", html or "").strip()

# =========================
# AI BLOG GENERATION
# =========================
def openai_generate(topic, products):
    product_list_text = "\n".join([f"- {p['title']} ({p['url']})" for p in products])

    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a JSON-only Shopify blog generator. Output must be valid JSON."},
            {"role": "user", "content": f"""
Write a Shopify blog post (800–1100 words) to increase organic traffic, interest, and conversions for Brand63.com.

Focus: NEWEST products and what's trending right now.
Topic anchor: "{topic}"

Products to feature (ONLY internal links, only these product URLs):
{product_list_text}

Rules:
- Use <h2>, <h3>, <p>, <ul><li> HTML formatting.
- No external links. No placeholders.
- Make the title match the products you were given.
- Include practical style/use ideas, gift angles, and a strong call-to-action at the end.
- Return JSON with keys: title, html, tags, excerpt, meta_description.
- tags should be comma-separated words/phrases (we will add " blog" automatically).
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
        max_completion_tokens=2200,
    )

    obj = json.loads(resp.choices[0].message.content)

    excerpt = (obj.get("excerpt") or "").strip()
    if not excerpt:
        excerpt = strip_tags(obj.get("html", ""))[:200].strip()
        if not excerpt:
            excerpt = "Fresh drops and curated picks from Brand63."

    meta = (obj.get("meta_description") or "").strip()
    if not meta:
        meta = excerpt
    meta = meta[:300]

    return obj["title"], obj["html"], obj["tags"], excerpt[:250], meta

# =========================
# PUBLISH BLOG (DRAFT)
# =========================
def publish_article(blog_id, title, body_html, meta_desc, tags, excerpt,
                    featured_image_src=None, featured_image_alt=None):

    meta_desc = (meta_desc or "").strip()[:300]
    excerpt = (excerpt or "").strip()[:250]
    if not excerpt:
        excerpt = "Fresh drops and curated picks from Brand63."

    cleaned_tags = ",".join(
        [t.strip().replace("#", "").replace("|", "") + " blog"
         for t in (tags or "").split(",") if t.strip()]
    )
    if not cleaned_tags:
        cleaned_tags = "brand63 blog, new arrivals blog, streetwear blog"

    article = {
        "article": {
            "title": title[:250],
            "author": AUTHOR_NAME,
            "tags": cleaned_tags,
            "body_html": body_html,
            "published": AUTO_PUBLISH,
            "excerpt": excerpt,
            "excerpt_html": f"<p>{excerpt}</p>",
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

# =========================
# MAIN
# =========================
def main():
    if not STORE or not TOKEN or not OPENAI_API_KEY:
        raise SystemExit("Missing env vars. Check SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_ACCESS_TOKEN, OPENAI_API_KEY.")

    blog_id = get_blog_id_by_handle(BLOG_HANDLE)

    newest_pool = get_newest_products_from_priority_collections()
    picks = choose_fresh_products(newest_pool, k=PICKS_PER_POST)

    # Topic should match what's actually selected
    topic = "Fresh Drops and New Arrivals"

    title, html, tags, excerpt, meta = openai_generate(topic, picks)

    image_blocks = "\n".join(build_image_html(p) for p in picks)
    combined_html = f"{html}\n<hr/>\n<section>{image_blocks}</section>"

    featured_src = picks[0]["image"] if picks else None
    featured_alt = picks[0]["title"] if picks else None

    try:
        result = publish_article(blog_id, title, combined_html, meta, tags, excerpt, featured_src, featured_alt)
        print("\n✅ Draft saved successfully:", result["article"]["title"])
        print("🆕 Picked products:")
        for p in picks:
            print("-", p["title"], "|", p["url"])
        print("📜 Shopify Response:")
        print(json.dumps(result, indent=2))
    except requests.exceptions.HTTPError as e:
        print("\n❌ Shopify rejected the blog post.")
        print("Response code:", e.response.status_code)
        print("Full message:", e.response.text)

if __name__ == "__main__":
    main()

