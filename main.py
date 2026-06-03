import os, json, random, re, requests
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
    created = shopify_post("blogs.json", {"blog": {"title": handle.capitalize(), "handle": handle}})
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
    data = shopify_get(f"collections/{collection_id}/products.json", params={"limit": limit})
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

def pick_topic_and_products():
    collections = get_all_collections()

    if not collections:
        raise RuntimeError("No Shopify collections found.")

    random.shuffle(collections)

    for chosen in collections:
        products = get_products_from_collection(chosen["id"], limit=20)

        if len(products) >= 1:
            picks = random.sample(products, k=min(3, len(products)))
            collection_url = f"https://{STORE.replace('.myshopify.com','')}.myshopify.com/collections/{chosen['handle']}"
            return chosen["title"], chosen["handle"], collection_url, picks

    raise RuntimeError("No collections with products and images were found.")

# ===== IMAGE / PRODUCT HTML =====
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
def openai_generate(collection_title, collection_url, products):
    product_list_text = "\n".join([
        f"- {p['title']} ({p['url']}) | Tags: {p.get('tags','')}"
        for p in products
    ])

    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
You are an expert SEO strategist, ecommerce copywriter, and content marketer for Brand63.com.

Main collection:
{collection_title}

Collection URL:
{collection_url}

Products from this exact collection:
{product_list_text}

Your goal:
Create a high-quality ecommerce blog post that can help Brand63 build search engine authority, attract interested shoppers, and guide readers toward the collection and products.

Important rules:
- Do NOT use the repeated title "Fresh Drops and New Arrivals."
- Create a unique SEO title based on the collection and shopper search intent.
- The blog must clearly match the collection and the products listed.
- Do not mention unrelated product types.
- Use natural keyword phrases shoppers may search for.
- Sound human, helpful, and confident.
- Make Brand63 sound like a trustworthy ecommerce brand for apparel, gifts, lifestyle products, and expressive style.
- Use internal links only.
- Include a link to the collection URL at least once.
- Include product links naturally.
- Write 1000 to 1400 words.
- Use HTML only inside the "html" field.
- Use <h2>, <h3>, <p>, <ul>, and <li>.
- Include a Brand63 authority section.
- Include a buyer-intent section.
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

Tag rules:
- tags should be comma-separated.
- Give 4 to 6 tags only.
- Do NOT add the word blog to tags. The code will do that later.

Excerpt rules:
- excerpt must be 1 to 2 short sentences.
- no HTML in excerpt.

Meta description rules:
- 120 to 160 characters.
"""

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Return only valid JSON. No markdown. No explanation."},
            {"role": "user", "content": prompt}
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

    if not excerpt:
        excerpt = clean_text(html, 250)

    if not meta_description:
        meta_description = clean_text(excerpt, 160)

    if not primary_keyword:
        primary_keyword = collection_title

    if "Fresh Drops and New Arrivals".lower() in title.lower():
        title = f"Best {collection_title} Picks from Brand63"

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

    collection_title, collection_handle, collection_url, picks = pick_topic_and_products()

    title, html, tags, excerpt, meta_description, primary_keyword = openai_generate(
        collection_title,
        collection_url,
        picks
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

