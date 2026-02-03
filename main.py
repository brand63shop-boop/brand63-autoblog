import os, json, random, re
from datetime import datetime, timedelta, timezone
import requests
from openai import OpenAI

# =========================
# CONFIG (EDIT IF NEEDED)
# =========================
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")                 # like brand63.myshopify.com
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

API_VERSION = "2023-10"
AUTHOR_NAME = os.getenv("AUTHOR_NAME", "Brand63")
BLOG_HANDLE = os.getenv("BLOG_HANDLE", "trendsetter-news")

# Keep drafts hidden until you approve
AUTO_PUBLISH = False

# Your public domain (used to build blog URLs for internal linking)
PUBLIC_DOMAIN = os.getenv("PUBLIC_DOMAIN", "www.brand63.com")

# How many newest products we consider "fresh"
NEWEST_POOL_SIZE = 500        # pull up to 500 newest active products
FRESH_SAMPLE_WINDOW = 120     # pick from the newest top 120 to strongly prefer new items
PICKS_PER_POST = 3

# How far ahead to start "holiday mode"
HOLIDAY_LOOKAHEAD_DAYS = 45

# Put your Shopify collection handles here (edit these to match your store)
# If a handle is wrong, the code will just fall back to newest products.
SEASON_COLLECTION_HANDLES = {
    "valentines": [
        "valentines-day",                 # example handle (edit to yours)
        "valentines-gifts",               # example handle (edit to yours)
    ],
    "black_history": [
        "black-history-vibe-collection",  # example handle (edit to yours)
        "black-history-vibe",             # example handle (edit to yours)
    ],
    # Optional evergreen collections you want to feature often:
    "evergreen_priority": [
        "top-new-arrivals",               # example handle (edit to yours)
        "womens-urban-style",             # example handle (edit to yours)
        "mens-urban-style",               # example handle (edit to yours)
    ],
}

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
    if blogs:
        return blogs[0].get("id")
    payload = {"blog": {"title": handle.replace("-", " ").title(), "handle": handle}}
    created = shopify_post("blogs.json", payload)
    return created["blog"]["id"]

def get_all_collections(limit=250):
    """Get both custom + smart collections so we don't miss anything."""
    collections = []

    # custom collections
    try:
        data = shopify_get("custom_collections.json", params={"limit": limit})
        for c in data.get("custom_collections", []):
            collections.append({"id": c["id"], "title": c["title"], "handle": c["handle"]})
    except Exception:
        pass

    # smart collections
    try:
        data = shopify_get("smart_collections.json", params={"limit": limit})
        for c in data.get("smart_collections", []):
            collections.append({"id": c["id"], "title": c["title"], "handle": c["handle"]})
    except Exception:
        pass

    # de-dup by handle
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
    collections = get_all_collections(limit=250)
    for c in collections:
        if (c.get("handle") or "").strip().lower() == handle:
            return c
    return None

def get_products_from_collection(collection_id, limit=250):
    data = shopify_get(f"collections/{collection_id}/products.json", params={"limit": limit})
    products = []
    for p in data.get("products", []):
        if p.get("status") and str(p.get("status")).lower() != "active":
            continue
        imgs = p.get("images", [])
        first_img = imgs[0]["src"] if imgs else None
        if not first_img:
            continue
        products.append({
            "id": p["id"],
            "title": p["title"],
            "handle": p["handle"],
            "created_at": p.get("created_at", ""),
            "url": f"https://{PUBLIC_DOMAIN}/products/{p['handle']}",
            "image": first_img,
            "tags": p.get("tags", ""),
            "vendor": p.get("vendor", "")
        })
    return products

def get_newest_active_products(limit=500):
    """Get newest active products store-wide (this is the main freshness fix)."""
    # Shopify caps to 250 per request
    take = min(250, limit)
    params = {"limit": take, "order": "created_at desc", "status": "active"}
    data = shopify_get("products.json", params=params)

    products = []
    for p in data.get("products", []):
        imgs = p.get("images", [])
        first_img = imgs[0]["src"] if imgs else None
        if not first_img:
            continue
        products.append({
            "id": p["id"],
            "title": p["title"],
            "handle": p["handle"],
            "created_at": p.get("created_at", ""),
            "url": f"https://{PUBLIC_DOMAIN}/products/{p['handle']}",
            "image": first_img,
            "tags": p.get("tags", ""),
            "vendor": p.get("vendor", "")
        })
    return products

def get_recent_blog_posts(blog_id, limit=8):
    """Used for interlinking (internal blog links)."""
    data = shopify_get(f"blogs/{blog_id}/articles.json", params={"limit": limit})
    posts = []
    for a in data.get("articles", []):
        handle = a.get("handle")
        title = a.get("title")
        if handle and title:
            posts.append({
                "title": title,
                "url": f"https://{PUBLIC_DOMAIN}/blogs/{BLOG_HANDLE}/{handle}"
            })
    return posts

# =========================
# DATE + HOLIDAY LOGIC
# =========================
def now_pacific():
    # America/Los_Angeles is UTC-8 in winter (Jan), UTC-7 in summer.
    # We'll approximate using local runner time in UTC and shift -8.
    return datetime.now(timezone.utc) + timedelta(hours=-8)

def within_days(target_date: datetime, days: int) -> bool:
    n = now_pacific().date()
    t = target_date.date()
    return 0 <= (t - n).days <= days

def get_season_mode():
    """
    Returns one of: 'black_history', 'valentines', or None.
    Priority:
      - Black History Month (Feb) takes priority
      - Late Jan can start Black History mode (ahead of Feb 1)
      - Valentines starts within lookahead window
    """
    n = now_pacific()
    year = n.year

    # Black History Month: all February, plus "ramp up" last 15 days of January
    feb_1 = datetime(year, 2, 1, tzinfo=timezone.utc) + timedelta(hours=-8)
    if n.month == 2 or (n.month == 1 and (feb_1.date() - n.date()).days <= 15):
        return "black_history"

    # Valentines: within lookahead window before Feb 14
    vday = datetime(year, 2, 14, tzinfo=timezone.utc) + timedelta(hours=-8)
    if within_days(vday, HOLIDAY_LOOKAHEAD_DAYS):
        return "valentines"

    return None

# =========================
# PICK TOPIC + PRODUCTS (NEW LOGIC)
# =========================
def sort_newest(products):
    # created_at is ISO string; sorting descending keeps newest first
    return sorted(products, key=lambda x: x.get("created_at", ""), reverse=True)

def choose_products_fresh(products, k=3):
    if not products:
        raise RuntimeError("No active products with images found.")
    products = sort_newest(products)
    window = products[:min(FRESH_SAMPLE_WINDOW, len(products))]
    return random.sample(window, k=min(k, len(window)))

def pick_topic_and_products(blog_id):
    """
    BEST logic:
      1) If in holiday mode AND you have holiday collections listed, pull from those collections.
      2) Else, strongly prefer newest products store-wide.
      3) Add internal blog links list for the AI to link to (interlinking).
    """
    season = get_season_mode()

    seasonal_products = []
    chosen_topic = None

    # 1) Try seasonal collections first if we’re in season
    if season and SEASON_COLLECTION_HANDLES.get(season):
        handles = SEASON_COLLECTION_HANDLES.get(season, [])
        for h in handles:
            c = get_collection_by_handle(h)
            if c:
                seasonal_products.extend(get_products_from_collection(c["id"], limit=250))

        seasonal_products = sort_newest(seasonal_products)

        if seasonal_products:
            chosen_topic = "Black History Vibe Collection" if season == "black_history" else "Valentine’s Day Style & Gifts"
            picks = choose_products_fresh(seasonal_products, k=PICKS_PER_POST)
            recent_posts = get_recent_blog_posts(blog_id, limit=8)
            return chosen_topic, picks, season, recent_posts

    # 2) Otherwise: newest products store-wide (this is the main freshness fix)
    newest = get_newest_active_products(limit=NEWEST_POOL_SIZE)
    newest = sort_newest(newest)

    if newest:
        chosen_topic = "Top New Arrivals"  # generic but accurate
        picks = choose_products_fresh(newest, k=PICKS_PER_POST)
        recent_posts = get_recent_blog_posts(blog_id, limit=8)
        return chosen_topic, picks, season, recent_posts

    # 3) Last resort: any collection at all
    collections = get_all_collections(limit=250)
    if not collections:
        raise RuntimeError("⚠️ No collections found in Shopify.")

    chosen = random.choice(collections)
    products = get_products_from_collection(chosen["id"], limit=250)
    if not products:
        raise RuntimeError(f"⚠️ No products found in collection: {chosen['title']}")

    chosen_topic = chosen["title"]
    picks = choose_products_fresh(products, k=PICKS_PER_POST)
    recent_posts = get_recent_blog_posts(blog_id, limit=8)
    return chosen_topic, picks, season, recent_posts

# =========================
# HTML BUILDERS
# =========================
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
def openai_generate(topic, products, season_mode, recent_blog_posts):
    product_list_text = "\n".join([f"- {p['title']} ({p['url']})" for p in products])

    blog_link_text = ""
    if recent_blog_posts:
        # Give AI a few internal blog links for interlinking
        blog_link_text = "\n".join([f"- {b['title']}: {b['url']}" for b in recent_blog_posts[:6]])

    season_note = ""
    if season_mode == "black_history":
        season_note = "Season context: Black History Month content. Keep it respectful, empowering, culture-forward, and product-relevant."
    elif season_mode == "valentines":
        season_note = "Season context: Valentine’s Day content. Keep it giftable, fun, and conversion-focused."

    client = OpenAI(api_key=OPENAI_API_KEY)

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a JSON-only Shopify blog generator. Output must be valid JSON."},
            {"role": "user", "content": f"""
Write a Shopify blog post (800–1100 words) to increase organic traffic, interest, and conversions for Brand63.com.

Topic/Collection focus: "{topic}"
{season_note}

Products to feature (ONLY internal links, only these product URLs):
{product_list_text}

Internal blog posts you can link to (optional, but include 1–2 of these if they fit naturally):
{blog_link_text}

Rules:
- Use <h2>, <h3>, <p>, <ul><li> HTML formatting.
- No external links. No placeholders.
- Add a strong call-to-action section at the end.
- Make the title match the products you were given.
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

    # Fallback excerpt if missing
    excerpt = (obj.get("excerpt") or "").strip()
    if not excerpt:
        excerpt = strip_tags(obj.get("html", ""))[:200].strip()
        if not excerpt:
            excerpt = "Fresh drops, style tips, and curated picks from Brand63."

    # Keep meta description safe length
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
        excerpt = "Fresh drops, style tips, and curated picks from Brand63."

    # Clean tags and append " blog"
    cleaned_tags = ",".join(
        [t.strip().replace("#", "").replace("|", "") + " blog"
         for t in (tags or "").split(",") if t.strip()]
    )
    if not cleaned_tags:
        cleaned_tags = "brand63 blog, style blog, new arrivals blog"

    article = {
        "article": {
            "title": title[:250],
            "author": AUTHOR_NAME,
            "tags": cleaned_tags,
            "body_html": body_html,
            "published": AUTO_PUBLISH,
            "excerpt": excerpt,
            "excerpt_html": f"<p>{excerpt}</p>",
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

    topic, picks, season_mode, recent_posts = pick_topic_and_products(blog_id)

    title, html, tags, excerpt, meta = openai_generate(topic, picks, season_mode, recent_posts)

    # Add image blocks at bottom
    image_blocks = "\n".join(build_image_html(p) for p in picks)
    combined_html = f"{html}\n<hr/>\n<section>{image_blocks}</section>"

    featured_src = picks[0]["image"] if picks else None
    featured_alt = picks[0]["title"] if picks else None

    try:
        result = publish_article(blog_id, title, combined_html, meta, tags, excerpt, featured_src, featured_alt)
        print("\n✅ Draft saved successfully:", result["article"]["title"])
        print("📜 Shopify Response:")
        print(json.dumps(result, indent=2))
        print("\n🧠 Season mode:", season_mode)
        print("🆕 Picked products:")
        for p in picks:
            print("-", p["title"], "|", p["url"])
    except requests.exceptions.HTTPError as e:
        print("\n❌ Shopify rejected the blog post.")
        print("Response code:", e.response.status_code)
        print("Full message:", e.response.text)

if __name__ == "__main__":
    main()

