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
    return f"""
<figure>
  <a href="{p['url']}" target="_self" rel="noopener">
    <img src="{p['image']}" alt="{alt}" loa


