import os, json, random, re, requests
from datetime import datetime
from openai import OpenAI

# ===== CONFIG =====
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_VERSION = "2023-10"
AUTHOR_NAME = "Brand63"
BLOG_HANDLE = "trendsetter-news"

AUTO_PUBLISH = False  # False = hidden draft until you approve

SESSION = requests.Session()
SESSION.headers.update({
    "X-Shopify-Access-Token": TOKEN,
    "Content-Type": "application/json",
    "Accept": "application/json"
})

# ===== SEASONAL SEO FOCUS =====
def get_current_season_keywords():
    month = datetime.utcnow().month

    if month in [1, 2]:
        return [
            "winter fashion", "new year style", "valentine gifts", "hoodies",
            "coffee mugs", "faith-based gifts", "urbanwear", "streetwear"
        ]

    if month in [3, 4]:
        return [
            "spring fashion", "easter gifts", "faith-based apparel", "spring outfits",
            "t-shirts", "tote bags", "printables", "instant downloads"
        ]

    if month in [5, 6]:
        return [
            "summer fashion", "fathers day gifts", "best gifts for dads",
            "athletes only", "mens urban style", "t-shirts", "coffee mugs",
            "boys clothing and gifts", "girls apparel and gifts", "summer outfits",
            "streetwear", "urban fashion", "t-shirts under 10"
        ]

    if month in [7, 8]:
        return [
            "back to school", "summer style", "kids apparel", "boys clothing",
            "girls apparel", "streetwear", "anime t-shirts", "urbanwear",
            "t-shirts", "tote bags"
        ]

    if month == 9:
        return [
            "fall fashion", "streetwear", "hoodie styling", "urbanwear",
            "mens urban style", "womens urban style", "coffee mugs", "tote bags"
        ]

    if month == 10:
        return [
            "halloween", "fall fashion", "graphic tees", "streetwear",
            "urban fashion", "coffee mugs", "tote bags"
        ]

    if month in [11, 12]:
        return [
            "holiday gifts", "christmas gifts", "black friday", "cyber monday",
            "coffee mugs", "faith-based gifts", "gifts for dads", "tote bags",
            "winter fashion", "hoodies", "streetwear"
        ]

    return ["streetwear", "urban fashion", "coffee mugs", "t-shirts"]


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

def clean_text(value, max_len=300):
    value = value or ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len]

def get_blog_id_by_handle(handle):
    data = shopify_get("blogs.json")
    blogs = data.get("blogs", [])

    for b in blogs:
        if b.get("handle") == handle:
            return b.get("id")

    if blogs:
        return blogs[0].get("id")

    created = shopify_post("blogs.json", {
        "blog": {
            "title": handle.capitalize(),
            "handle": handle
        }
    })

    return created["blog"]["id"]


# ===== COLLECTIONS =====
def get_all_collections(limit=250):
    collections = []

    custom = shopify_get("custom_collections.json", params={"limit": limit})
    for c in custom.get("custom_collections", []):
        collections.append({
            "id": c["id"],
            "title": c["title"],
            "handle": c["handle"],
            "type": "custom"
        })

    smart = shopify_get("smart_collections.json", params={"limit": limit})
    for c in smart.get("smart_collections", []):
        collections.append({
            "id": c["id"],
            "title": c["title"],
            "handle": c["handle"],
            "type": "smart"
        })

    return collections

def get_products_from_collection(collection_id, limit=20):
    data = shopify_get(
        f"collections/{collection_id}/products.json",
        params={"limit": limit}
    )

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
            "url": f"https://{STORE.replace('.myshopify.com','')}.myshopify.com/products/{p['handle']}",
            "image": first_img,
            "tags": p.get("tags", ""),
            "body_html": p.get("body_html", "")
        })

    return products


# ===== SMART COLLECTION PICKER =====
def collection_matches_season(collection, season_keywords):
    title = collection.get("title", "").lower()
    handle = collection.get("handle", "").lower()
    combined = f"{title} {handle}"

    return any(keyword.lower() in combined for keyword in season_keywords)

def avoid_bad_seasonal_collection(collection):
    month = datetime.utcnow().month
    title = collection.get("title", "").lower()
    handle = collection.get("handle", "").lower()
    combined = f"{title} {handle}"

    blocked_now = []

    if month in [1, 2, 3, 4, 5, 6, 7, 8, 9]:
        blocked_now.extend(["christmas", "holiday", "xmas", "winter"])

    if month not in [9, 10]:
        blocked_now.extend(["halloween", "trick-or-treat"])

    if month not in [5, 6]:
        blocked_now.extend(["fathers day", "father's day"])

    if month not in [2, 3, 4]:
        blocked_now.extend(["easter"])

    return any(word in combined for word in blocked_now)

def pick_topic_and_products():
    collections = get_all_collections()

    if not collections:
        raise RuntimeError("No Shopify collections found.")

    season_keywords = get_current_season_keywords()

    good_collections = [
        c for c in collections
        if collection_matches_season(c, season_keywords)
        and not avoid_bad_seasonal_collection(c)
    ]

    if not good_collections:
        good_collections = [
            c for c in collections
            if not avoid_bad_seasonal_collection(c)
        ]

    if not good_collections:
        good_collections = collections

    random.shuffle(good_collections)

    for chosen in good_collections:
        products = get_products_from_collection(chosen["id"], limit=20)

        if len(products) >= 1:
            picks = random.sample(products, k=min(3, len(products)))
            collection_url = f"https://{STORE.replace('.myshopify.com','')}.myshopify.com/collections/{chosen['handle']}"
            return chosen["title"], chosen["handle"], collection_url, picks, season_keywords

    raise RuntimeError("No collections with products and images were found.")


# ===== PRODUCT HTML =====
def build_image_html(p, primary_keyword):
    alt = f"{primary_keyword} - {p['title']} by {AUTHOR_NAME}"

    return f"""
<figure>
  <a href="{p['url']}" target="_self" rel="noopener">
    <img src="{p['image']}" alt="{alt}" loading="lazy" />
  </a>
  <figcaption><a href="{p['url']}" target="_self">Shop {p['title']}</a></figcaption>
</figure>
""".strip()

def build_product_section(products, primary_keyword):
    blocks = "\n".join(build_image_html(p, primary_keyword) for p in products)

    return f"""
<hr/>
<h2>Shop Featured Brand63 Picks</h2>
<section>
{blocks}
</section>
""".strip()


# ===== AI BLOG GENERATION =====
def openai_generate(collection_title, collection_url, products, season_keywords):
    product_list_text = "\n".join([
        f"- {p['title']} ({p['url']}) | Tags: {p.get('tags','')}"
        for p in products
    ])

    seasonal_focus = ", ".join(season_keywords)

    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
You are an expert SEO strategist, ecommerce copywriter, and content marketer for Brand63.com.

Brand63 is an ecommerce store focused on affordable apparel, expressive streetwear, gifts, lifestyle products, printables, mugs, bags, and personality-driven style.

Main collection:
{collection_title}

Collection URL:
{collection_url}

Current seasonal SEO focus:
{seasonal_focus}

Products from this exact collection:
{product_list_text}

Your job:
Create a strong SEO blog post that can help Brand63 build search authority, attract shoppers, and guide people to the collection and featured products.

Critical rules:
- Do NOT use the title or heading "Fresh Drops and New Arrivals."
- Do NOT make the title generic.
- Do NOT mention unrelated product categories.
- The blog must match the collection and featured products.
- Use real shopper-style keyword phrases.
- Make Brand63 sound like a trusted ecommerce brand.
- Use internal links only.
- Include the collection URL naturally at least once.
- Include product links naturally.
- Write 1000 to 1400 words.
- Use HTML only inside the "html" field.
- Use <h2>, <h3>, <p>, <ul>, and <li>.
- Include a section explaining who this collection is best for.
- Include a section with buyer-intent language.
- Include a Brand63 authority section.
- Include a strong call to action.
- Include 4 FAQ questions and answers inside the article.

Return valid JSON only with these exact keys:
title
html
tags
excerpt
meta_description
primary_keyword
secondary_keywords

Title rules:
- SEO-friendly.
- Clear and specific.
- 55 to 70 characters if possible.
- Must not say Fresh Drops or New Arrivals.

Tag rules:
- tags should be comma-separated.
- Give 4 to 6 tags only.
- Do NOT add the word blog. The code will add it.

Excerpt rules:
- excerpt must be 1 to 2 short sentences.
- no HTML in excerpt.

Meta description rules:
- 120 to 160 characters.
"""

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": "Return only valid JSON. No markdown. No explanation."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "brand63_blog_post",
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "html": {"type": "string"},
                        "tags": {"type": "string"},
                        "excerpt": {"type": "string"},
                        "meta_description": {"type": "string"},
                        "primary_keyword": {"type": "string"},
                        "secondary_keywords": {"type": "string"}
                    },
                    "required": [
                        "title",
                        "html",
                        "tags",
                        "excerpt",
                        "meta_description",
                        "primary_keyword",
                        "secondary_keywords"
                    ]
                }
            }
        },
        max_completion_tokens=2500,
    )

    obj = json.loads(resp.choices[0].message.content)

    title = clean_text(obj.get("title"), 250)
    html = obj.get("html", "").strip()
    tags = obj.get("tags", "").strip()
    excerpt = clean_text(obj.get("excerpt"), 250)
    meta_description = clean_text(obj.get("meta_description"), 160)
    primary_keyword = clean_text(obj.get("primary_keyword"), 100)

    bad_titles = [
        "fresh drops",
        "new arrivals",
        "latest arrivals",
        "collection spotlight"
    ]

    if any(bad in title.lower() for bad in bad_titles):
        title = f"Best {collection_title} Picks from Brand63"

    if not excerpt:
        excerpt = clean_text(html, 250)

    if not meta_description:
        meta_description = clean_text(excerpt, 160)

    if not primary_keyword:
        primary_keyword = collection_title

    if not html:
        raise RuntimeError("AI did not generate blog content.")

    return title, html, tags, excerpt, meta_description, primary_keyword


# ===== TAG CLEANER =====
def clean_tags(tags, collection_title):
    raw_tags = []

    if tags:
        raw_tags.extend(tags.split(","))

    raw_tags.append(collection_title)

    cleaned = []

    for tag in raw_tags:
        tag = tag.strip()
        tag = tag.replace("#", "").replace("|", "").replace("/", " ")
        tag = re.sub(r"\s+", " ", tag)

        if not tag:
            continue

        if not tag.lower().endswith(" blog"):
            tag = f"{tag} blog"

        if tag.lower() not in [t.lower() for t in cleaned]:
            cleaned.append(tag)

    return ", ".join(cleaned[:6])


# ===== PUBLISH BLOG =====
def publish_article(blog_id, title, body_html, tags, excerpt, featured_image_src=None, featured_image_alt=None):
    safe_excerpt = clean_text(excerpt, 250)

    if not safe_excerpt:
        safe_excerpt = "Read the latest Brand63 style guide, product highlights, and shopping inspiration."

    article = {
        "article": {
            "title": title[:250],
            "author": AUTHOR_NAME,
            "tags": tags,
            "body_html": body_html,
            "published": AUTO_PUBLISH,
            "summary_html": f"<p>{safe_excerpt}</p>"
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
        raise SystemExit("Missing required environment variables.")

    blog_id = get_blog_id_by_handle(BLOG_HANDLE)

    collection_title, collection_handle, collection_url, picks, season_keywords = pick_topic_and_products()

    title, html, tags, excerpt, meta_description, primary_keyword = openai_generate(
        collection_title,
        collection_url,
        picks,
        season_keywords
    )

    product_section = build_product_section(picks, primary_keyword)

    seo_note = f"""
<p><strong>Explore more:</strong> See the full <a href="{collection_url}" target="_self">Brand63 {collection_title} collection</a> for more styles, gifts, and new finds.</p>
"""

    combined_html = f"""
{html}
{seo_note}
{product_section}
"""

    cleaned_tags = clean_tags(tags, collection_title)

    featured_src = picks[0]["image"] if picks else None
    featured_alt = f"{primary_keyword} - {picks[0]['title']} by {AUTHOR_NAME}" if picks else title

    result = publish_article(
        blog_id,
        title,
        combined_html,
        cleaned_tags,
        excerpt,
        featured_src,
        featured_alt
    )

    print("✅ Draft saved:", result["article"]["title"])
    print("Collection used:", collection_title)
    print("Primary keyword:", primary_keyword)
    print("Tags:", cleaned_tags)

if __name__ == "__main__":
    main()
