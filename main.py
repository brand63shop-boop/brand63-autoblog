import os, json, random, re, requests
from openai import OpenAI

# ===== CONFIG =====
STORE = os.getenv("SHOPIFY_STORE_DOMAIN")
TOKEN = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_VERSION = "2023-10"
AUTHOR_NAME = "Brand63"
BLOG_HANDLE = "trendsetter-news"
AUTO_PUBLISH = False

PUBLIC_DOMAIN = "https://www.brand63.com"
KLAVIYO_EMBED = '<div class="klaviyo-form-SWqvMf"></div>'

SESSION = requests.Session()
SESSION.headers.update({
    "X-Shopify-Access-Token": TOKEN,
    "Content-Type": "application/json",
    "Accept": "application/json"
})

HIGH_PRIORITY_KEYWORDS = [
    "Best Gifts For Dads", "Coffee Mugs", "Anime T-Shirts",
    "Women's Urban Style", "Men's Urban Style", "Urban Fashion Trends",
    "Streetwear Gift Guide", "T-shirts Under $10", "Best Totes and Bags",
    "Faith-Based Apparel and Gifts", "Boys Clothing and Gifts",
    "Top Girl's Apparel and Gifts", "Infant and Toddler Apparel and Gifts",
    "The Black Girl Mug Collection"
]

MEDIUM_PRIORITY_KEYWORDS = [
    "Athletes Only", "Design Your Own Apparel", "Top New Arrivals",
    "Fashion Trends", "Clearance Sale", "Instant Downloads",
    "Printables", "Head Gear"
]

LOW_PRIORITY_KEYWORDS = [
    "OOTD", "NJ WEAR 2008", "Fresh Friday For Men",
    "Tuesday Fashion Fix", "Fashion Sale", "BTS Merch Sale"
]

# ===== HELPERS =====
def clean_text(value, max_len=300):
    value = value or ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len]

def slug_words(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return [w for w in text.split() if len(w) > 2]

def shopify_get(path, params=None):
    url = f"https://{STORE}/admin/api/{API_VERSION}/{path}"
    r = SESSION.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def shopify_post(path, payload):
    url = f"https://{STORE}/admin/api/{API_VERSION}/{path}"
    r = SESSION.post(url, data=json.dumps(payload), timeout=120)
    if not r.ok:
        print("Shopify error response:", r.text)
    r.raise_for_status()
    return r.json()

def product_public_url(handle):
    return f"{PUBLIC_DOMAIN}/products/{handle}"

def collection_public_url(handle):
    return f"{PUBLIC_DOMAIN}/collections/{handle}"

def safe_link_title(text):
    return clean_text(text.replace('"', "'"), 120)

# ===== KEYWORDS =====
def load_keywords():
    try:
        with open("keywords.csv", "r", encoding="utf-8") as f:
            keywords = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        keywords = []

    if not keywords:
        keywords = HIGH_PRIORITY_KEYWORDS + MEDIUM_PRIORITY_KEYWORDS + LOW_PRIORITY_KEYWORDS

    return keywords

def choose_keyword():
    keywords = load_keywords()

    high = [k for k in keywords if k in HIGH_PRIORITY_KEYWORDS]
    medium = [k for k in keywords if k in MEDIUM_PRIORITY_KEYWORDS]
    low = [k for k in keywords if k in LOW_PRIORITY_KEYWORDS]

    roll = random.random()

    if roll < 0.80 and high:
        return random.choice(high)
    if roll < 0.95 and medium:
        return random.choice(medium)
    if low:
        return random.choice(low)

    return random.choice(keywords)

# ===== SHOPIFY BLOG =====
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

def score_collection(keyword, collection):
    keyword_words = set(slug_words(keyword))
    collection_words = set(slug_words(collection["title"] + " " + collection["handle"]))

    if not keyword_words:
        return 0

    score = len(keyword_words.intersection(collection_words))

    if keyword.lower() in collection["title"].lower():
        score += 5

    return score

def choose_collection_for_keyword(keyword):
    collections = get_all_collections()

    if not collections:
        raise RuntimeError("No Shopify collections found.")

    scored = [(score_collection(keyword, c), c) for c in collections]
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best_collection = scored[0]

    if best_score > 0:
        return best_collection

    return random.choice(collections)

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
            "url": product_public_url(p["handle"]),
            "image": first_img,
            "tags": p.get("tags", ""),
            "body_html": p.get("body_html", "")
        })

    return products

def pick_topic_and_products():
    keyword = choose_keyword()
    collection = choose_collection_for_keyword(keyword)
    products = get_products_from_collection(collection["id"], limit=20)

    if not products:
        all_collections = get_all_collections()
        random.shuffle(all_collections)

        for c in all_collections:
            products = get_products_from_collection(c["id"], limit=20)
            if products:
                collection = c
                break

    if not products:
        raise RuntimeError("No collection products with images found.")

    picks = random.sample(products, k=min(3, len(products)))
    collection_url = collection_public_url(collection["handle"])

    return keyword, collection["title"], collection["handle"], collection_url, picks

# ===== HTML BUILDERS =====
def build_image_html(p, primary_keyword):
    alt = f"{primary_keyword} - {p['title']} by Brand63"
    link_title = safe_link_title(f"Shop {p['title']} at Brand63")

    return f"""
<figure>
  <a href="{p['url']}" target="_blank" rel="noopener noreferrer" title="{link_title}">
    <img src="{p['image']}" alt="{alt}" loading="lazy" />
  </a>
  <figcaption>
    <a href="{p['url']}" target="_blank" rel="noopener noreferrer" title="{link_title}">
      Shop {p['title']}
    </a>
  </figcaption>
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

def build_authority_block():
    return """
<hr/>
<h2>Why Shop Brand63?</h2>
<p>Brand63 is an online destination for affordable apparel, gifts, drinkware, lifestyle products, printables, and urban fashion inspiration. Our goal is to help shoppers find expressive products that feel personal, stylish, and budget-friendly.</p>
""".strip()

def add_missing_link_attributes(html):
    # Adds target, rel, and title to links if AI forgot.
    def fix_link(match):
        full_tag = match.group(0)
        href = match.group(1)

        if "target=" not in full_tag:
            full_tag = full_tag.replace("<a ", '<a target="_blank" ')
        if "rel=" not in full_tag:
            full_tag = full_tag.replace("<a ", '<a rel="noopener noreferrer" ')
        if "title=" not in full_tag:
            title = safe_link_title("Shop Brand63 products and collections")
            full_tag = full_tag.replace("<a ", f'<a title="{title}" ')

        return full_tag

    html = re.sub(r'<a\s+[^>]*href="([^"]+)"[^>]*>', fix_link, html)
    return html

# ===== AI GENERATION =====
def openai_generate(keyword, collection_title, collection_url, products):
    product_list_text = "\n".join([
        f"- {p['title']} ({p['url']}) | Tags: {p.get('tags','')}"
        for p in products
    ])

    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
You are an expert SEO strategist and ecommerce copywriter for Brand63.com.

Primary keyword phrase:
{keyword}

Related Shopify collection:
{collection_title}

Collection URL:
{collection_url}

Products from this collection:
{product_list_text}

Write a high-quality ecommerce blog post designed to attract search engine traffic and help shoppers discover Brand63 products.

Important:
- Do NOT use "Fresh Drops and New Arrivals" as the title.
- The title must start with or clearly include the primary keyword.
- Use related keyword phrases naturally.
- Make Brand63 sound trustworthy, helpful, affordable, and style-focused.
- Focus only on this collection and these products.
- Do not mention unrelated products.
- Use internal links only.
- Use https://www.brand63.com links only.
- All links must include target="_blank", rel="noopener noreferrer", and a descriptive title attribute.
- Link to the collection URL once.
- Link to each product naturally.
- Write 1000 to 1400 words.
- Use HTML inside the html field only.
- Use <h2>, <h3>, <p>, <ul>, and <li>.
- Include a buyer-intent section.
- Include a Brand63 authority section.
- Include 4 FAQs.
- End with a strong shop-now call to action.

Return valid JSON only with these exact keys:
title
html
tags
excerpt
meta_description
primary_keyword
secondary_keywords

Excerpt:
- 12 to 20 words only.
- No HTML.

Meta description:
- 120 to 155 characters only.
- No HTML.

Tags:
- 4 to 6 comma-separated tags.
- Do NOT add the word blog. The code will do that later.
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
        max_completion_tokens=2600,
    )

    obj = json.loads(resp.choices[0].message.content)

    title = clean_text(obj.get("title"), 70)
    html = obj.get("html", "").strip()
    tags = obj.get("tags", "").strip()
    excerpt = clean_text(obj.get("excerpt"), 140)
    meta_description = clean_text(obj.get("meta_description"), 155)
    primary_keyword = clean_text(obj.get("primary_keyword"), 100)

    if not html:
        raise RuntimeError("AI did not generate blog content.")

    html = html.replace("brand63.myshopify.com", "www.brand63.com")
    html = add_missing_link_attributes(html)

    if "fresh drops and new arrivals" in title.lower():
        title = f"{keyword}: Brand63 Style Guide"

    if not title.lower().startswith(keyword.lower().split()[0]):
        title = f"{keyword}: Brand63 Style Guide"

    if not excerpt:
        excerpt = clean_text(f"Shop {keyword} styles, gifts, and finds from Brand63.", 140)

    excerpt_words = excerpt.split()
    if len(excerpt_words) > 20:
        excerpt = " ".join(excerpt_words[:20]) + "."

    if not meta_description:
        meta_description = clean_text(
            f"Discover {keyword} ideas, apparel, gifts, and lifestyle finds from Brand63. Shop affordable styles today.",
            155
        )

    if not primary_keyword:
        primary_keyword = keyword

    return title, html, tags, excerpt, meta_description, primary_keyword

# ===== TAGS =====
def clean_tags(tags, keyword, collection_title):
    raw_tags = []

    if tags:
        raw_tags.extend(tags.split(","))

    raw_tags.append(keyword)
    raw_tags.append(collection_title)

    cleaned = []

    for tag in raw_tags:
        tag = tag.strip()
        tag = tag.replace("#", "").replace("|", " ").replace("/", " ")
        tag = re.sub(r"\s+", " ", tag)

        if not tag:
            continue

        if not tag.lower().endswith(" blog"):
            tag = f"{tag} blog"

        if tag.lower() not in [t.lower() for t in cleaned]:
            cleaned.append(tag)

    return ", ".join(cleaned[:6])

# ===== META DESCRIPTION =====
def add_seo_metafields(blog_id, article_id, meta_description, title):
    meta_description = clean_text(meta_description, 155)
    title = clean_text(title, 70)

    if not meta_description:
        return

    metafields = [
        {
            "metafield": {
                "namespace": "global",
                "key": "description_tag",
                "value": meta_description,
                "type": "single_line_text_field"
            }
        },
        {
            "metafield": {
                "namespace": "global",
                "key": "title_tag",
                "value": title,
                "type": "single_line_text_field"
            }
        }
    ]

    for payload in metafields:
        try:
            shopify_post(f"blogs/{blog_id}/articles/{article_id}/metafields.json", payload)
            print(f"✅ SEO metafield saved: {payload['metafield']['key']}")
        except Exception as e:
            print(f"⚠️ SEO metafield skipped: {payload['metafield']['key']} - {e}")

# ===== PUBLISH =====
def publish_article(blog_id, title, body_html, tags, excerpt, meta_description, featured_image_src=None, featured_image_alt=None):
    safe_excerpt = clean_text(excerpt, 140)

    if not safe_excerpt:
        safe_excerpt = "Shop Brand63 style inspiration, gifts, apparel, and everyday finds."

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

    result = shopify_post(f"blogs/{blog_id}/articles.json", article)

    article_id = result["article"]["id"]
    add_seo_metafields(blog_id, article_id, meta_description, title)

    return result

# ===== MAIN =====
def main():
    if not STORE or not TOKEN or not OPENAI_API_KEY:
        raise SystemExit("Missing required environment variables.")

    blog_id = get_blog_id_by_handle(BLOG_HANDLE)

    keyword, collection_title, collection_handle, collection_url, picks = pick_topic_and_products()

    title, html, tags, excerpt, meta_description, primary_keyword = openai_generate(
        keyword,
        collection_title,
        collection_url,
        picks
    )

    authority_block = build_authority_block()
    product_section = build_product_section(picks, primary_keyword)

    collection_title_attr = safe_link_title(f"Shop the {collection_title} collection at Brand63")

    collection_link = f"""
<p><strong>Explore more:</strong> Browse the full
<a href="{collection_url}" target="_blank" rel="noopener noreferrer" title="{collection_title_attr}">
Brand63 {collection_title} collection
</a>.</p>
"""

    combined_html = f"""
{html}
{collection_link}
{authority_block}
{product_section}
<hr/>
{KLAVIYO_EMBED}
"""

    combined_html = combined_html.replace("brand63.myshopify.com", "www.brand63.com")
    combined_html = add_missing_link_attributes(combined_html)

    cleaned_tags = clean_tags(tags, keyword, collection_title)

    featured_src = picks[0]["image"] if picks else None
    featured_alt = f"{primary_keyword} - {picks[0]['title']} by Brand63" if picks else title

    result = publish_article(
        blog_id,
        title,
        combined_html,
        cleaned_tags,
        excerpt,
        meta_description,
        featured_src,
        featured_alt
    )

    print("✅ Draft saved:", result["article"]["title"])
    print("Keyword used:", keyword)
    print("Collection used:", collection_title)
    print("Primary keyword:", primary_keyword)
    print("Excerpt:", excerpt)
    print("Meta description:", meta_description)
    print("Tags:", cleaned_tags)

if __name__ == "__main__":
    main()
