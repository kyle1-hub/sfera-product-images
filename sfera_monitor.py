import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from datetime import datetime
from functools import lru_cache
from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urljoin, urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont


CONFIG_PATH = Path(__file__).with_name("config.json")
EXAMPLE_CONFIG_PATH = Path(__file__).with_name("config.example.json")
SITE_META = {
    "sfera": {
        "display_name": "Sfera",
        "marker": "NUEVO",
        "base_url": "https://www.sfera.com/es/mujer/bisuteria/",
    },
    "bijou": {
        "display_name": "Bijou Brigitte",
        "marker": "Neu",
        "base_url": "https://www.bijou-brigitte.com/neu/",
    },
    "bershka": {
        "display_name": "Bershka",
        "marker": "Newly appeared",
        "base_url": "https://www.bershka.com/gb/",
    },
}


def load_config():
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH
    with path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    env_webhook = os.environ.get("WECOM_WEBHOOK")
    if env_webhook:
        cfg["wecom_webhook"] = env_webhook
    cfg["state_dir"] = str((Path(__file__).parent / cfg.get("state_dir", "state")).resolve())
    return cfg


class Store:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "sfera_products.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY,
                name TEXT,
                price TEXT,
                url TEXT,
                image_url TEXT,
                category TEXT,
                first_seen TEXT,
                last_seen TEXT,
                image_path TEXT
            )
            """
        )
        try:
            self.conn.execute("ALTER TABLE products ADD COLUMN site TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        self.conn.commit()

    def mark_seen(self, product):
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute("SELECT product_id FROM products WHERE product_id = ?", (product["product_id"],))
        exists = cur.fetchone() is not None
        if exists:
            self.conn.execute(
                """
                UPDATE products
                SET name = ?, price = ?, url = ?, image_url = ?, category = ?, site = ?, last_seen = ?, image_path = COALESCE(?, image_path)
                WHERE product_id = ?
                """,
                (
                    product.get("name", ""),
                    product.get("price", ""),
                    product.get("url", ""),
                    product.get("image_url", ""),
                    product.get("category", ""),
                    product.get("site", ""),
                    now,
                    product.get("image_path"),
                    product["product_id"],
                ),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO products(product_id, name, price, url, image_url, category, site, first_seen, last_seen, image_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product["product_id"],
                    product.get("name", ""),
                    product.get("price", ""),
                    product.get("url", ""),
                    product.get("image_url", ""),
                    product.get("category", ""),
                    product.get("site", ""),
                    now,
                    now,
                    product.get("image_path"),
                ),
            )
        self.conn.commit()
        return not exists


def normalize_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def product_id_for(product):
    key = product.get("url") or "|".join([product.get("category", ""), product.get("name", ""), product.get("price", "")])
    return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:16]


def api_headers(category_slug):
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "es-ES",
        "referer": f"https://www.sfera.com/es/mujer/bisuteria/{category_slug}/",
        "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }


def api_url(category_slug, page):
    suffix = f"/{page}/" if page > 1 else "/"
    return f"https://www.sfera.com/es/api/sfera-es/firefly/products_list/mujer/bisuteria/{category_slug}{suffix}?showDimensions=none"


def fetch_json(url, headers, retries=3):
    return json.loads(fetch_text(url, headers, retries=retries))


def fetch_text(url, headers, retries=3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=45) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="ignore")
        except HTTPError as exc:
            last_error = exc
            if exc.code != 403 or attempt == retries:
                raise
            time.sleep(2 * attempt)
        except Exception as exc:
            last_error = exc
            if attempt == retries:
                raise
            time.sleep(2 * attempt)
    raise last_error


def product_url(item, category_slug):
    if item.get("_base_url"):
        return item["_base_url"]
    if item.get("_uri"):
        return f"https://www.sfera.com{item['_uri']}"
    code = item.get("code_a") or item.get("id") or ""
    name = item.get("name") or item.get("title") or ""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if code and slug:
        return f"https://www.sfera.com/es/mujer/{quote(code)}-{quote(slug)}/?parentCategoryId=5002"
    return f"https://www.sfera.com/es/mujer/bisuteria/{category_slug}/"


def image_from_entry(entry):
    sources = entry.get("sources") or {}
    for size in ("zoom", "big", "medium", "small"):
        source = sources.get(size)
        if source and "no-image" not in source:
            return source
    default_source = entry.get("default_source")
    if default_source and "no-image" not in default_source:
        return default_source
    return ""


def unique_image_entries(entries):
    seen = set()
    unique = []
    for entry in entries:
        source = image_from_entry(entry)
        if source and source not in seen:
            seen.add(source)
            unique.append(entry)
    return unique


def product_image_entries(color):
    entries = []
    entries.extend(color.get("all_preview_images") or [])
    entries.extend(color.get("all_images") or [])
    for variant in color.get("variants") or []:
        entries.extend(variant.get("all_preview_images") or [])
        entries.extend(variant.get("all_images") or [])
    return unique_image_entries(entries)


def image_referer(image_source):
    host = urlparse(image_source).netloc.lower()
    if "bijou-brigitte.com" in host:
        return "https://www.bijou-brigitte.com/"
    if "bershka" in host or "inditex" in host:
        return "https://www.bershka.com/"
    return "https://www.sfera.com/"


@lru_cache(maxsize=512)
def has_white_background(image_source):
    if not image_source:
        return False
    try:
        req = Request(
            image_source,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
                "Referer": image_referer(image_source),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9",
            },
        )
        with urlopen(req, timeout=20) as resp:
            image = Image.open(BytesIO(resp.read())).convert("RGB")
        image.thumbnail((120, 120))
        width, height = image.size
        pixels = []
        for x in range(width):
            pixels.append(image.getpixel((x, 0)))
            pixels.append(image.getpixel((x, height - 1)))
        for y in range(height):
            pixels.append(image.getpixel((0, y)))
            pixels.append(image.getpixel((width - 1, y)))
        if not pixels:
            return False
        white_pixels = sum(1 for r, g, b in pixels if r >= 238 and g >= 238 and b >= 238)
        return white_pixels / len(pixels) >= 0.82
    except Exception:
        return False


def preferred_plain_image(entries):
    candidates = [image for image in entries if image.get("is_plain") and not image.get("is_look")]
    if not candidates:
        return ""
    first_source = image_from_entry(candidates[0])
    if has_white_background(first_source):
        return first_source
    for image in candidates[1:]:
        source = image_from_entry(image)
        if has_white_background(source):
            return source
    return first_source


def image_url(item):
    colors = item.get("_my_colors") or []
    for color in colors:
        source = preferred_plain_image(product_image_entries(color))
        if source:
            return source
    for color in colors:
        previews = color.get("all_preview_images") or color.get("all_images") or []
        source = preferred_plain_image(previews)
        if source:
            return source
        variants = color.get("variants") or []
        for variant in variants:
            source = preferred_plain_image(variant.get("all_images") or [])
            if source:
                return source
    for key in ("default_image", "image", "priority_image"):
        value = item.get(key)
        if isinstance(value, str) and value.startswith("http") and "no-image" not in value:
            return value
        if isinstance(value, dict):
            source = image_from_entry(value)
            if source:
                return source
    for color in colors:
        previews = color.get("all_preview_images") or color.get("all_images") or []
        for image in previews:
            source = image_from_entry(image)
            if source:
                return source
        if color.get("image") and "no-image" not in color.get("image"):
            return color["image"]
    product_id = item.get("id") or item.get("variant", "").strip()
    if product_id:
        return f"https://dam.elcorteingles.es/producto/www-{product_id}-00.jpg?impolicy=Resize&width=967&height=1200"
    return ""


def product_price(item):
    price = item.get("price") or {}
    final_price = price.get("f_price")
    if isinstance(final_price, (int, float)):
        return f"{final_price:.2f} €".replace(".", ",")
    colors = item.get("_my_colors") or []
    for color in colors:
        for variant in color.get("variants") or []:
            value = variant.get("price")
            if isinstance(value, (int, float)):
                return f"{value:.2f} €".replace(".", ",")
    return ""


def map_api_product(item, category_name, category_slug):
    name = normalize_text(item.get("name") or item.get("title") or "").upper()
    return {
        "site": "sfera",
        "category": category_name,
        "name": name,
        "price": product_price(item),
        "url": product_url(item, category_slug),
        "image_url": image_url(item),
        "product_id": item.get("code_a") or item.get("id") or product_id_for({"category": category_name, "name": name}),
        "is_new": bool((item.get("badges") or {}).get("new")),
    }


def extract_products_from_payload(payload):
    data = payload.get("data") or {}
    products = data.get("products") or []
    datalayer = data.get("paginatedDatalayer") or {}
    pagination = data.get("pagination") or {}
    page_info = datalayer.get("page") or {}
    total_pages = page_info.get("total_pages") or pagination.get("totalPages") or pagination.get("_total_pages") or 1
    return products, int(total_pages or 1)


def download_image(product, state_dir):
    image_url = product.get("image_url")
    if not image_url or image_url.startswith("data:"):
        return None
    image_dir = Path(state_dir) / "images" / datetime.now().strftime("%Y%m%d")
    image_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urlparse(image_url).path).suffix or ".jpg"
    output = image_dir / f"{product['product_id']}{suffix}"
    if output.exists():
        return str(output)
    req = Request(
        image_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Referer": product.get("url") or "https://www.sfera.com/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    with urlopen(req, timeout=30) as resp:
        output.write_bytes(resp.read())
    return str(output)


def scrape_sfera(config):
    all_new_products = []
    for category in config["categories"]:
        category_name = category["name"] if isinstance(category, dict) else category
        category_slug = category["slug"] if isinstance(category, dict) else category.lower().replace(" ", "-")
        headers = api_headers(category_slug)
        print(f"[抓取] {category_name}")
        first_payload = fetch_json(api_url(category_slug, 1), headers)
        page_items, total_pages = extract_products_from_payload(first_payload)
        category_products = []
        for item in page_items:
            category_products.append(map_api_product(item, category_name, category_slug))
        print(f"[分页] {category_name}: 1/{total_pages}，本页 {len(page_items)} 个")
        for page in range(2, total_pages + 1):
            payload = fetch_json(api_url(category_slug, page), headers)
            page_items, _ = extract_products_from_payload(payload)
            print(f"[分页] {category_name}: {page}/{total_pages}，本页 {len(page_items)} 个")
            for item in page_items:
                category_products.append(map_api_product(item, category_name, category_slug))
            time.sleep(0.5)
        new_products = [product for product in category_products if product.get("is_new")]
        print(f"[结果] {category_name}: 总商品 {len(category_products)} 个，NUEVO {len(new_products)} 个")
        all_new_products.extend(new_products)
    return all_new_products


def bijou_headers():
    return {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "accept-language": "de-DE,de;q=0.9,en;q=0.8",
        "referer": "https://www.bijou-brigitte.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }


def bijou_page_url(page):
    base = "https://www.bijou-brigitte.com/neu/"
    return base if page <= 1 else f"{base}?p={page}"


def html_attr(fragment, name):
    match = re.search(rf"{name}=[\"']([^\"']*)[\"']", fragment, re.I)
    return unescape(match.group(1)).strip() if match else ""


def strip_html(value):
    return normalize_text(unescape(re.sub(r"<[^>]+>", " ", value or "")))


def unique_site_urls(urls, base_url):
    seen = set()
    unique = []
    for url in urls:
        url = unescape(str(url or "")).strip()
        if not url or url.startswith("data:"):
            continue
        url = urljoin(base_url, url)
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def unique_urls(urls):
    return unique_site_urls(urls, "https://www.bijou-brigitte.com/")


def extract_bijou_total_pages(html):
    pages = [1]
    for match in re.finditer(r'[?&]p=(\d+)', html):
        pages.append(int(match.group(1)))
    return max(pages)


def bijou_image_candidates_from_html(html, product_number=""):
    urls = []
    for match in re.finditer(r'<img\b[^>]*>', html, re.I | re.S):
        tag = match.group(0)
        if product_number and product_number not in tag:
            continue
        src = html_attr(tag, "src")
        if "/media/" in src:
            urls.append(src)
        srcset = html_attr(tag, "srcset")
        for part in srcset.split(","):
            candidate = part.strip().split(" ")[0]
            if "/media/" in candidate or "/thumbnail/" in candidate:
                urls.append(candidate)
    for match in re.finditer(r"https?://www\.bijou-brigitte\.com/(?:media|thumbnail)/[^\"'<>\s]+", html):
        url = match.group(0)
        if not product_number or product_number in url:
            urls.append(url)
    return unique_urls(urls)


def bijou_detail_image_candidates(product_url, product_number):
    if not product_url:
        return []
    try:
        html = fetch_text(product_url, bijou_headers())
    except Exception as exc:
        print(f"[Bijou 图片详情页失败] {product_url}: {exc}")
        return []
    return bijou_image_candidates_from_html(html, product_number)


def bijou_preferred_image(listing_candidates, product_url, product_number):
    listing_candidates = unique_urls(listing_candidates)
    if not listing_candidates:
        return ""
    first_source = listing_candidates[0]
    if has_white_background(first_source):
        return first_source
    candidates = unique_urls(listing_candidates[1:] + bijou_detail_image_candidates(product_url, product_number))
    for source in candidates:
        if has_white_background(source):
            return source
    return first_source


def first_white_background_image(candidates):
    candidates = unique_site_urls(candidates, "https://www.bershka.com/")
    if not candidates:
        return ""
    first_source = candidates[0]
    if has_white_background(first_source):
        return first_source
    for source in candidates[1:]:
        if has_white_background(source):
            return source
    return first_source


def refine_product_image(product):
    site = product.get("site")
    candidates = product.get("image_candidates") or [product.get("image_url")]
    if site == "bijou":
        product["image_url"] = bijou_preferred_image(candidates, product.get("url", ""), product.get("source_id", "")) or product.get("image_url", "")
    elif site == "bershka":
        product["image_url"] = first_white_background_image(candidates) or product.get("image_url", "")
    return product


def bijou_big_category(data_group):
    top_group = (data_group or "").split(";")[0].strip().lower()
    if top_group == "accessoires":
        return "Neue Accessoires"
    return "Neuer Schmuck"


def extract_bijou_products(html):
    products = []
    chunks = re.split(r'<div\s+class="cms-listing-col[^>]*>', html, flags=re.I)
    for chunk in chunks:
        if "product-box" not in chunk:
            continue
        if not re.search(r'class="flag\s+new"[^>]*>\s*Neu\s*<', chunk, re.I):
            continue
        product_number = html_attr(chunk, "data-number")
        data_group = html_attr(chunk, "data-group")
        name = html_attr(chunk, "data-name") or strip_html(re.search(r'<div[^>]+class="product-name"[^>]*>(.*?)</div>', chunk, re.I | re.S).group(1) if re.search(r'<div[^>]+class="product-name"[^>]*>(.*?)</div>', chunk, re.I | re.S) else "")
        price_value = html_attr(chunk, "data-price")
        price = f"{float(price_value):.2f} €".replace(".", ",") if re.fullmatch(r"\d+(?:\.\d+)?", price_value or "") else price_value
        link_match = re.search(r"<a\b[^>]*href=[\"']([^\"']+)[\"']", chunk, re.I)
        product_url_value = urljoin("https://www.bijou-brigitte.com/", unescape(link_match.group(1))) if link_match else "https://www.bijou-brigitte.com/neu/"
        candidates = bijou_image_candidates_from_html(chunk, product_number)
        fallback_id = product_id_for({"url": product_url_value, "name": name, "price": price})
        products.append(
            {
                "site": "bijou",
                "category": bijou_big_category(data_group),
                "name": normalize_text(name),
                "price": price,
                "url": product_url_value,
                "image_url": candidates[0] if candidates else "",
                "image_candidates": candidates,
                "source_id": product_number,
                "product_id": f"bijou:{product_number or fallback_id}",
                "is_new": True,
            }
        )
    return products


def scrape_bijou(config):
    headers = bijou_headers()
    print("[抓取] Bijou Brigitte Neu")
    first_html = fetch_text(bijou_page_url(1), headers)
    total_pages = extract_bijou_total_pages(first_html)
    products = extract_bijou_products(first_html)
    print(f"[分页] Bijou Brigitte Neu: 1/{total_pages}，本页 {len(products)} 个")
    for page in range(2, total_pages + 1):
        html = fetch_text(bijou_page_url(page), headers)
        page_products = extract_bijou_products(html)
        print(f"[分页] Bijou Brigitte Neu: {page}/{total_pages}，本页 {len(page_products)} 个")
        products.extend(page_products)
        time.sleep(0.5)
    unique = {}
    for product in products:
        unique[product["product_id"]] = product
    products = list(unique.values())
    print(f"[结果] Bijou Brigitte Neu: Neu {len(products)} 个")
    return products


def bershka_headers(referer=None):
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-GB,en;q=0.9",
        "origin": "https://www.bershka.com",
        "referer": referer or "https://www.bershka.com/gb/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }


def extract_bershka_category_id(category_url):
    match = re.search(r"[/-][nc](\d+)\.html", category_url or "")
    return match.group(1) if match else ""


def bershka_products_array_url(store_id, catalog_id, category_id, language_id=-1, country="gb", version=3):
    return (
        f"https://www.bershka.com/itxrest/{version}/catalog/store/{store_id}/{catalog_id}/productsArray"
        f"?categoryId={quote(str(category_id))}&languageId={quote(str(language_id))}&appId=1&country={quote(country)}"
    )


def bershka_api_urls(config, category):
    explicit = category.get("api_url") or category.get("products_api_url")
    if explicit:
        return [explicit]
    store_id = config.get("store_id")
    catalog_id = config.get("catalog_id")
    category_id = category.get("category_id") or extract_bershka_category_id(category.get("url", ""))
    language_id = config.get("language_id", -1)
    country = config.get("country", "gb")
    if not store_id or not catalog_id or not category_id:
        return []
    urls = []
    urls.append(
        f"https://www.bershka.com/itxrest/3/catalog/store/{store_id}/{catalog_id}/category/{category_id}/product"
        f"?languageId={quote(str(language_id))}&appId=1&showProducts=false"
    )
    for version in (3, 2):
        urls.append(bershka_products_array_url(store_id, catalog_id, category_id, language_id, country, version))
        urls.append(
            f"https://www.bershka.com/itxrest/{version}/catalog/store/{store_id}/{catalog_id}/category/{category_id}/product"
            f"?languageId={quote(str(language_id))}&appId=1&country={quote(country)}"
        )
        urls.append(
            f"https://www.bershka.com/itxrest/{version}/catalog/store/{country}/bershka/{store_id}/{catalog_id}/productsArray"
            f"?categoryId={quote(str(category_id))}&languageId={quote(str(language_id))}&appId=1"
        )
    return urls


def fetch_bershka_page(category_url, headers):
    html = fetch_text(category_url, headers, retries=1)
    if "/_sec/verify?provider=interstitial" not in html or '"bm-verify"' not in html:
        return html
    try:
        i = int(html.split("var i = ", 1)[1].split(";", 1)[0])
        left = html.split('Number("', 1)[1].split('"', 1)[0]
        right = html.split('+ "', 1)[1].split('"', 1)[0]
        token = html.split('"bm-verify": "', 1)[1].split('"', 1)[0]
        data = json.dumps({"bm-verify": token, "pow": i + int(left + right)}).encode("utf-8")
        verify_req = Request(
            "https://www.bershka.com/_sec/verify?provider=interstitial",
            data=data,
            headers={**headers, "Content-Type": "application/json", "Referer": category_url},
            method="POST",
        )
        opener = urlopen(verify_req, timeout=30)
        opener.read()
        opener.close()
        return fetch_text(category_url, headers, retries=1)
    except Exception:
        return html


def discover_bershka_api_context(category_url, headers):
    try:
        html = fetch_bershka_page(category_url, headers)
    except Exception as exc:
        raise RuntimeError(f"Bershka 页面/API 参数自动发现失败，请在 config.json 配置 store_id/catalog_id 或 category.api_url：{exc}") from exc
    context = {}
    store_match = re.search(r"storeId=(\d+)", html) or re.search(r"DEFAULT_STORE_ID\s*:\s*(\d+)", html, re.I)
    if store_match:
        context["store_id"] = store_match.group(1)
    language_match = re.search(r"DEFAULT_LANGUAGE_ID\s*:\s*(-?\d+)", html, re.I)
    if language_match:
        context["language_id"] = language_match.group(1)
    patterns = {
        "store_id": r'["\']storeId["\']\s*[:=]\s*["\']?(\d+)',
        "catalog_id": r'["\']catalogId["\']\s*[:=]\s*["\']?(\d+)',
        "language_id": r'["\']languageId["\']\s*[:=]\s*["\']?(-?\d+)',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, html, re.I)
        if match:
            context[key] = match.group(1)
    if not context.get("store_id") or not context.get("catalog_id"):
        raise RuntimeError("Bershka 页面未暴露完整 API 参数；请在 config.json 配置 store_id/catalog_id 或 category.api_url。")
    return context


def iter_nested(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_nested(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_nested(child)


def bershka_product_like(item):
    if not isinstance(item, dict):
        return False
    keys = {str(key).lower() for key in item}
    has_id = any(key in keys for key in ("id", "productid", "product_id", "bundleproductid"))
    has_name = any(key in keys for key in ("name", "title", "productname", "displayname"))
    has_media = any(key in keys for key in ("image", "imageurl", "mainimage", "xmedia", "media", "colors", "bundlecolors"))
    return has_id and (has_name or has_media)


def extract_bershka_commercial_ids(payload):
    ids = []
    for item in iter_nested(payload):
        if not isinstance(item, dict):
            continue
        cc_id = item.get("ccId")
        if cc_id:
            ids.append(cc_id)
        ids.extend(item.get("ccIds") or [])
        for component in item.get("commercialComponentIds") or []:
            if isinstance(component, dict) and component.get("ccId"):
                ids.append(component["ccId"])
    unique = []
    seen = set()
    for item in ids:
        value = str(item)
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def extract_bershka_products_from_payload(payload):
    products = []
    seen = set()
    for item in iter_nested(payload):
        if not bershka_product_like(item):
            continue
        source_id = str(item.get("id") or item.get("productId") or item.get("product_id") or item.get("bundleProductId") or "")
        key = source_id or json.dumps(item, sort_keys=True, ensure_ascii=False)[:200]
        if key in seen:
            continue
        seen.add(key)
        products.append(item)
    return products


def fetch_bershka_products_array(product_ids, config, category_url):
    if not product_ids:
        return []
    store_id = config.get("store_id")
    catalog_id = config.get("catalog_id")
    language_id = config.get("language_id", -1)
    if not store_id or not catalog_id:
        return []
    products = []
    headers = bershka_headers(category_url)
    for start in range(0, len(product_ids), 20):
        chunk = product_ids[start : start + 20]
        product_ids_param = quote(",".join(chunk), safe=",")
        url = (
            f"https://www.bershka.com/itxrest/3/catalog/store/{store_id}/{catalog_id}/productsArray"
            f"?languageId={quote(str(language_id))}&appId=1&productIds={product_ids_param}"
        )
        payload = fetch_json(url, headers, retries=3)
        products.extend(payload.get("products") or extract_bershka_products_from_payload(payload))
        time.sleep(1)
    return products


def fetch_bershka_category_products(category, config):
    category_name = category.get("name") or category.get("category_id") or "unknown"
    category_url = category.get("url") or config.get("base_url") or "https://www.bershka.com/gb/"
    headers = bershka_headers(category_url)
    urls = bershka_api_urls(config, category)
    active_config = config
    if not urls:
        discovered = discover_bershka_api_context(category_url, headers)
        active_config = dict(config)
        active_config.update({key: value for key, value in discovered.items() if key in ("store_id", "catalog_id", "language_id")})
        urls = bershka_api_urls(active_config, category)
    errors = []
    for url in urls:
        try:
            print(f"[Bershka] 请求 {category_name}: {url}")
            payload = fetch_json(url, headers, retries=3)
            products = extract_bershka_products_from_payload(payload)
            if products:
                print(f"[Bershka] {category_name}: 直接解析商品 {len(products)} 个")
                return products
            product_ids = extract_bershka_commercial_ids(payload)
            if product_ids:
                print(f"[Bershka] {category_name}: 解析到商品 ID {len(product_ids)} 个，继续请求 productsArray")
                time.sleep(2)
                products = fetch_bershka_products_array(product_ids, active_config, category_url)
                if products:
                    print(f"[Bershka] {category_name}: productsArray 返回商品 {len(products)} 个")
                    return products
                errors.append(f"{url}: 解析到 {len(product_ids)} 个商品 ID，但 productsArray 未返回商品")
                continue
            errors.append(f"{url}: 返回 JSON 但未解析到商品或商品 ID")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("Bershka 分类抓取失败：" + "；".join(errors))


def first_value(item, keys):
    for key in keys:
        value = item.get(key) if isinstance(item, dict) else None
        if value not in (None, ""):
            return value
    return ""


def find_first_nested(item, keys):
    lower_keys = {key.lower() for key in keys}
    for value in iter_nested(item):
        if not isinstance(value, dict):
            continue
        for key, child in value.items():
            if str(key).lower() in lower_keys and child not in (None, ""):
                return child
    return ""


def bershka_price(item):
    for key in ("currentPrice", "salePrice", "price", "oldPrice", "minPrice"):
        value = find_first_nested(item, [key])
        if isinstance(value, str):
            return normalize_text(value)
        if isinstance(value, (int, float)):
            amount = value / 100 if value >= 1000 else value
            return f"£{amount:.2f}"
        if isinstance(value, dict):
            formatted = first_value(value, ["formatted", "value", "current", "amount"])
            if isinstance(formatted, str):
                return normalize_text(formatted)
            if isinstance(formatted, (int, float)):
                amount = formatted / 100 if formatted >= 1000 else formatted
                return f"£{amount:.2f}"
    return ""


def bershka_absolute_url(value, base_url="https://www.bershka.com/gb/"):
    value = unescape(str(value or "")).strip()
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http"):
        return value
    return urljoin(base_url, value)


def bershka_image_from_media(media):
    if isinstance(media, str):
        return bershka_absolute_url(media)
    if not isinstance(media, dict):
        return ""
    for key in ("url", "imageUrl", "path", "src", "href", "set", "deliveryUrl"):
        value = media.get(key)
        if isinstance(value, str) and ("/" in value or value.startswith("http")):
            return bershka_absolute_url(value)
    extra = media.get("extraInfo") or {}
    if isinstance(extra, dict):
        for key in ("deliveryUrl", "url", "path"):
            value = extra.get(key)
            if isinstance(value, str):
                return bershka_absolute_url(value)
    return ""


def bershka_image_candidates(item):
    candidates = []
    for key in ("image", "imageUrl", "mainImage", "thumbnail", "url"):
        value = item.get(key) if isinstance(item, dict) else None
        if isinstance(value, str) and re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)|/photos/|/assets/|/static/", value, re.I):
            candidates.append(bershka_absolute_url(value))
        elif isinstance(value, dict):
            candidate = bershka_image_from_media(value)
            if candidate:
                candidates.append(candidate)
    for value in iter_nested(item):
        if isinstance(value, dict):
            candidate = bershka_image_from_media(value)
            if candidate and re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)|/photos/|/assets/|/static/", candidate, re.I):
                candidates.append(candidate)
        elif isinstance(value, str) and re.search(r"https?://[^\s\"']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\"']*)?$", value, re.I):
            candidates.append(value)
    return unique_site_urls(candidates, "https://www.bershka.com/gb/")


def bershka_product_url(item, category_url):
    for key in ("productUrl", "detailUrl", "seoUrl", "url", "href"):
        value = first_value(item, [key])
        if isinstance(value, str) and value:
            if re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", value, re.I):
                continue
            return bershka_absolute_url(value, "https://www.bershka.com/gb/")
    product_id = str(first_value(item, ["id", "productId", "product_id", "bundleProductId"]) or "")
    if product_id:
        return re.sub(r"\.html(?:\?.*)?$", f"p{product_id}.html", category_url)
    return category_url


def map_bershka_product(item, category_name, category_url, config):
    source_id = str(first_value(item, ["id", "productId", "product_id", "bundleProductId"]) or "")
    name = normalize_text(str(first_value(item, ["name", "title", "productName", "displayName"]) or find_first_nested(item, ["name", "title"]) or ""))
    product_url_value = bershka_product_url(item, category_url)
    if not source_id:
        match = re.search(r"p(\d+)\.html", product_url_value)
        source_id = match.group(1) if match else ""
    fallback_id = product_id_for({"url": product_url_value, "category": category_name, "name": name, "price": bershka_price(item)})
    candidates = bershka_image_candidates(item)
    return {
        "site": "bershka",
        "category": category_name,
        "name": name,
        "price": bershka_price(item),
        "url": product_url_value,
        "image_url": candidates[0] if candidates else "",
        "image_candidates": candidates,
        "source_id": source_id,
        "product_id": f"bershka:{source_id or fallback_id}",
        "is_new": True,
    }


def scrape_bershka(config):
    products = []
    categories = config.get("categories") or []
    if not categories:
        raise RuntimeError("Bershka 未配置 categories")
    for category in categories:
        category_name = category.get("name") if isinstance(category, dict) else str(category)
        category_url = category.get("url") if isinstance(category, dict) else config.get("base_url", "https://www.bershka.com/gb/")
        category_id = category.get("category_id") or extract_bershka_category_id(category_url) if isinstance(category, dict) else ""
        print(f"[抓取] Bershka {category_name} {category_id}")
        raw_products = fetch_bershka_category_products(category if isinstance(category, dict) else {"name": category_name, "url": category_url}, config)
        mapped = [map_bershka_product(item, category_name, category_url, config) for item in raw_products]
        mapped = [product for product in mapped if product.get("product_id") and (product.get("name") or product.get("image_url"))]
        print(f"[结果] Bershka {category_name}: 当前商品 {len(mapped)} 个")
        products.extend(mapped)
        time.sleep(0.5)
    unique = {}
    for product in products:
        unique[product["product_id"]] = product
    return list(unique.values())


def scrape_site(site_key, config):
    if site_key == "sfera":
        return scrape_sfera(config)
    if site_key == "bijou":
        return scrape_bijou(config)
    if site_key == "bershka":
        return scrape_bershka(config)
    raise ValueError(f"未知站点：{site_key}")


def post_wecom(webhook, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(webhook, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_wecom(webhook, content):
    return post_wecom(webhook, {"msgtype": "markdown", "markdown": {"content": content}})


def wecom_robot_key(webhook):
    query = parse_qs(urlparse(webhook).query)
    keys = query.get("key") or []
    if not keys or not keys[0]:
        raise RuntimeError("企业微信 webhook 缺少 key，无法上传文件")
    return keys[0]


def upload_wecom_file(webhook, file_path):
    key = wecom_robot_key(webhook)
    boundary = "----SferaWeComBoundary" + hashlib.md5(str(time.time()).encode()).hexdigest()
    path = Path(file_path)
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"{path.name}\"\r\nContent-Type: application/zip\r\n\r\n".encode("utf-8"),
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    req = Request(
        f"https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key={key}&type=file",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("errcode") != 0:
        raise RuntimeError(f"企业微信文件上传失败：{payload}")
    return payload["media_id"]


def send_wecom_file(webhook, file_path):
    media_id = upload_wecom_file(webhook, file_path)
    return post_wecom(webhook, {"msgtype": "file", "file": {"media_id": media_id}})


def safe_filename(value, fallback="product"):
    name = normalize_text(value) or fallback
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(" ._")
    return name[:90] or fallback


def save_product_image_as_jpg(product, target_dir, index):
    source_path = download_image(product, target_dir)
    if not source_path:
        return None
    target_dir = Path(target_dir)
    base = safe_filename(product.get("name") or product.get("product_id") or f"product_{index}")
    output = target_dir / f"{base}.jpg"
    counter = 2
    while output.exists():
        output = target_dir / f"{base}_{counter}.jpg"
        counter += 1
    image = Image.open(source_path)
    image.load()
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        background.alpha_composite(image)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")
    image.save(output, format="JPEG", quality=95, subsampling=0)
    return output


def build_product_zip_bundle(products, state_dir, site_url, site_name="Sfera", marker="NUEVO", max_products_per_zip=None):
    today = datetime.now().strftime("%Y%m%d")
    site_slug = safe_filename(site_name, "site").replace(" ", "_")
    bundle_root = Path(state_dir) / "wecom_zips" / f"{site_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    bundle_root.mkdir(parents=True, exist_ok=True)
    category_zips = []
    category_counts = []
    grouped = group_by_category(products)
    for category, category_products in grouped.items():
        count = len(category_products)
        category_counts.append((category, count))
        category_name = safe_filename(category, "category")
        chunks = [category_products]
        if max_products_per_zip and count > max_products_per_zip:
            chunks = [category_products[i : i + max_products_per_zip] for i in range(0, count, max_products_per_zip)]
        for chunk_index, chunk_products in enumerate(chunks, 1):
            suffix = f"_第{chunk_index}包" if len(chunks) > 1 else ""
            chunk_count = len(chunk_products)
            category_dir = bundle_root / f"{category_name}_{chunk_count}款_{marker}{suffix}"
            category_dir.mkdir(parents=True, exist_ok=True)
            for index, product in enumerate(chunk_products, 1):
                try:
                    save_product_image_as_jpg(product, category_dir, index)
                except Exception as exc:
                    print(f"[图片保存失败] {product.get('name')}: {exc}")
            category_zip = bundle_root / f"{category_name}_{chunk_count}款_{marker}{suffix}.zip"
            with zipfile.ZipFile(category_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for image_path in sorted(category_dir.glob("*.jpg")):
                    zf.write(image_path, image_path.name)
            category_zips.append(category_zip)
    master_zip = bundle_root / f"{site_slug}_网站上新_{len(products)}款_{today}.zip"
    with zipfile.ZipFile(master_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for category_zip in category_zips:
            zf.write(category_zip, category_zip.name)
    return master_zip, category_zips, category_counts


def build_zip_bundle_message(products, category_counts, site_url, site_name="Sfera", marker="NUEVO"):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"**{site_name} 网站产品上新提醒**",
        f"> 日期：{today}",
        f"> 网站：{site_url}",
        f"> 本次 {marker} 商品合计：{len(products)} 款",
        "",
        "**按品类打包：**",
    ]
    for category, count in category_counts:
        lines.append(f"- {category}：{count} 款")
    lines.extend(
        [
            "",
            "下面发送的是压缩包；如果总包超过企业微信大小限制，会按大类自动拆分发送。每张图片已转换为标准 JPG，并以产品名称命名。",
        ]
    )
    return "\n".join(lines)


def send_wecom_zip_bundle(webhook, products, state_dir, site_url, site_name="Sfera", marker="NUEVO"):
    max_bytes = 19 * 1024 * 1024
    master_zip, category_zips, category_counts = build_product_zip_bundle(products, state_dir, site_url, site_name, marker)
    bundle_root = master_zip.parent
    message = build_zip_bundle_message(products, category_counts, site_url, site_name, marker)
    text_result = send_wecom(webhook, message)
    sent_files = []
    if master_zip.stat().st_size <= max_bytes:
        file_result = send_wecom_file(webhook, master_zip)
        sent_files.append({"path": str(master_zip), "result": file_result})
    else:
        shutil.rmtree(bundle_root, ignore_errors=True)
        master_zip, category_zips, category_counts = build_product_zip_bundle(products, state_dir, site_url, site_name, marker, max_products_per_zip=80)
        bundle_root = master_zip.parent
        for category_zip in category_zips:
            file_result = send_wecom_file(webhook, category_zip)
            sent_files.append({"path": str(category_zip), "result": file_result})
    ok = text_result.get("errcode") == 0 and all(item["result"].get("errcode") == 0 for item in sent_files)
    if ok:
        shutil.rmtree(bundle_root, ignore_errors=True)
        cleanup = "deleted"
    else:
        cleanup = "kept"
    return {"message": text_result, "files": sent_files, "zip": str(master_zip), "cleanup": cleanup}


def send_wecom_news(webhook, products, title_prefix="Sfera NUEVO"):
    articles = []
    for product in products[:8]:
        title = product.get("name") or product.get("product_id") or "Sfera 新品"
        category = product.get("category") or ""
        price = product.get("price") or ""
        description = "｜".join(part for part in [category, price, "NUEVO"] if part)
        articles.append(
            {
                "title": f"{title_prefix}｜{title}"[:128],
                "description": description[:512],
                "url": product.get("url") or "https://www.sfera.com/es/mujer/bisuteria/",
                "picurl": product.get("image_url") or "",
            }
        )
    return post_wecom(webhook, {"msgtype": "news", "news": {"articles": articles}})


def load_font(size, bold=False):
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def fit_text(text, font, max_width):
    text = text or ""
    if len(text) <= 1:
        return text
    while text and font.getlength(text) > max_width:
        text = text[:-1]
    return text if font.getlength(text) <= max_width else text[:1]


def make_product_list_image(products, state_dir, title="Sfera NUEVO 商品测试"):
    products = products[:8]
    width = 900
    row_h = 150
    header_h = 90
    padding = 24
    output = Path(state_dir) / f"wecom_products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    canvas = Image.new("RGB", (width, header_h + row_h * len(products)), "white")
    draw = ImageDraw.Draw(canvas)
    title_font = load_font(30, bold=True)
    name_font = load_font(24, bold=True)
    meta_font = load_font(20)
    draw.rectangle([0, 0, width, header_h], fill=(40, 40, 40))
    draw.text((padding, 24), title, fill="white", font=title_font)
    for index, product in enumerate(products):
        y = header_h + index * row_h
        if index % 2 == 0:
            draw.rectangle([0, y, width, y + row_h], fill=(248, 248, 248))
        img_box = (padding, y + 18, padding + 110, y + 128)
        try:
            image_path = product.get("image_path") or download_image(product, state_dir)
            thumb = Image.open(image_path).convert("RGB")
            thumb.thumbnail((110, 110))
            bg = Image.new("RGB", (110, 110), "white")
            bg.paste(thumb, ((110 - thumb.width) // 2, (110 - thumb.height) // 2))
            canvas.paste(bg, (img_box[0], img_box[1]))
        except Exception:
            draw.rectangle(img_box, outline=(210, 210, 210), fill=(245, 245, 245))
            draw.text((img_box[0] + 30, img_box[1] + 42), "NO IMG", fill=(120, 120, 120), font=meta_font)
        text_x = padding + 135
        name = fit_text(product.get("name") or product.get("product_id") or "Sfera 新品", name_font, width - text_x - padding)
        meta = "｜".join(part for part in [product.get("category"), product.get("price"), "NUEVO"] if part)
        draw.text((text_x, y + 28), name, fill=(20, 20, 20), font=name_font)
        draw.text((text_x, y + 75), meta, fill=(90, 90, 90), font=meta_font)
    canvas.save(output, format="JPEG", quality=88)
    return str(output)


def send_wecom_image(webhook, image_path):
    data = Path(image_path).read_bytes()
    payload = {
        "msgtype": "image",
        "image": {
            "base64": base64.b64encode(data).decode("ascii"),
            "md5": hashlib.md5(data).hexdigest(),
        },
    }
    return post_wecom(webhook, payload)


def send_template_card(webhook, title, desc, image_url, action_url):
    payload = {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "news_notice",
            "source": {"desc": "Sfera 新品监控"},
            "main_title": {"title": title[:36], "desc": desc[:64]},
            "card_image": {"url": image_url, "aspect_ratio": 2.0},
            "card_action": {"type": 1, "url": action_url},
        },
    }
    return post_wecom(webhook, payload)


def send_batch_template_cards(webhook, products, state_dir, public_base_url=None):
    results = []
    for batch_index in range(0, len(products), 8):
        batch = products[batch_index : batch_index + 8]
        batch_no = batch_index // 8 + 1
        image_path = make_product_list_image(batch, state_dir, f"Sfera NUEVO 新品 第 {batch_no} 批")
        image_url = public_base_url.rstrip("/") + "/" + Path(image_path).name if public_base_url else "https://picsum.photos/600/300.jpg"
        title = f"Sfera 新品提醒｜第 {batch_no} 批｜{len(batch)} 个"
        desc_items = []
        for index, product in enumerate(batch[:8], 1):
            name = product.get("name") or product.get("product_id") or "Sfera 新品"
            desc_items.append(f"{index}. {name[:18]}")
        desc = "；".join(desc_items)
        action_url = batch[0].get("url") or "https://www.sfera.com/es/mujer/bisuteria/"
        results.append(send_template_card(webhook, title, desc, image_url, action_url))
        time.sleep(0.5)
    return results


def upload_tmpfiles(image_path):
    boundary = "----SferaBoundary" + hashlib.md5(str(time.time()).encode()).hexdigest()
    path = Path(image_path)
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\nContent-Type: image/jpeg\r\n\r\n".encode("utf-8"),
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    req = Request(
        "https://tmpfiles.org/api/v1/upload",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    url = payload.get("data", {}).get("url", "")
    if not url:
        raise RuntimeError(f"tmpfiles 上传失败：{payload}")
    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/")


def send_wecom_news_with_uploaded_images(webhook, products, state_dir, title_prefix="Sfera NUEVO"):
    uploaded = []
    for product in products[:8]:
        local_path = download_image(product, state_dir)
        public_url = upload_tmpfiles(local_path)
        item = dict(product)
        item["image_url"] = public_url
        uploaded.append(item)
        time.sleep(0.5)
    return send_wecom_news(webhook, uploaded, title_prefix)


def build_message(new_products, site_url="https://www.sfera.com/es/mujer/bisuteria/", site_name="Sfera", marker="NUEVO"):
    today = datetime.now().strftime("%Y-%m-%d")
    if not new_products:
        return "\n".join(
            [
                f"**{site_name} 网站产品上新提醒**",
                f"> 日期：{today}",
                f"> 网站：{site_url}",
                f"> 今日没有新增 {marker} 商品。",
            ]
        )
    lines = [f"{site_name} 新品监控发现 <font color=\"warning\">{len(new_products)}</font> 个新品", f"> {today}", ""]
    for idx, p in enumerate(new_products[:20], 1):
        name = p.get("name") or "未识别名称"
        price = p.get("price") or "未识别价格"
        category = p.get("category") or "未识别分类"
        url = p.get("url") or ""
        if url:
            lines.append(f"{idx}. **{category}**｜[{name}]({url})｜{price}")
        else:
            lines.append(f"{idx}. **{category}**｜{name}｜{price}")
    if len(new_products) > 20:
        lines.append(f"\n还有 {len(new_products) - 20} 个商品未在消息中展开。")
    return "\n".join(lines)


def build_batch_links_message(products, batch_no=1, total_batches=1):
    lines = [f"**Sfera NUEVO 新品｜第 {batch_no}/{total_batches} 批**", ""]
    for idx, product in enumerate(products, 1):
        name = product.get("name") or product.get("product_id") or "Sfera 新品"
        category = product.get("category") or ""
        price = product.get("price") or ""
        url = product.get("url") or ""
        meta = "｜".join(part for part in [category, price, "NUEVO"] if part)
        if url:
            lines.append(f"{idx}. [{name}]({url})")
        else:
            lines.append(f"{idx}. {name}")
        if meta:
            lines.append(f"   > {meta}")
    return "\n".join(lines)


def send_batch_image_and_links(webhook, products, state_dir):
    results = []
    total_batches = (len(products) + 7) // 8
    for start in range(0, len(products), 8):
        batch = products[start : start + 8]
        batch_no = start // 8 + 1
        image_path = make_product_list_image(batch, state_dir, f"Sfera NUEVO 新品 第 {batch_no}/{total_batches} 批")
        results.append(send_wecom_image(webhook, image_path))
        results.append(send_wecom(webhook, build_batch_links_message(batch, batch_no, total_batches)))
        time.sleep(0.5)
    return results


def make_category_grid_image(products, state_dir, category):
    products = products[:8]
    width = 900
    image_h = 760
    name_h = 92
    header_h = 96
    padding = 28
    card_gap = 26
    card_w = width - padding * 2
    height = header_h + padding + len(products) * (image_h + name_h) + max(0, len(products) - 1) * card_gap + padding
    output = Path(state_dir) / f"sfera_{category.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    title_font = load_font(36, bold=True)
    name_font = load_font(30, bold=True)
    draw.rectangle([0, 0, width, header_h], fill=(20, 20, 20))
    draw.text((padding, 26), f"Sfera NUEVO｜{category}｜{len(products)} 款", fill="white", font=title_font)
    for index, product in enumerate(products):
        x = padding
        y = header_h + padding + index * (image_h + name_h + card_gap)
        draw.rectangle([x, y, x + card_w, y + image_h + name_h], fill=(248, 248, 248), outline=(230, 230, 230))
        image_box = (x + 18, y + 18, x + card_w - 18, y + image_h - 18)
        try:
            image_path = product.get("image_path") or download_image(product, state_dir)
            image = Image.open(image_path).convert("RGB")
            image.thumbnail((image_box[2] - image_box[0], image_box[3] - image_box[1]), Image.Resampling.LANCZOS)
            bg = Image.new("RGB", (image_box[2] - image_box[0], image_box[3] - image_box[1]), "white")
            bg.paste(image, ((bg.width - image.width) // 2, (bg.height - image.height) // 2))
            canvas.paste(bg, (image_box[0], image_box[1]))
        except Exception:
            draw.rectangle(image_box, fill=(245, 245, 245), outline=(210, 210, 210))
            draw.text((image_box[0] + 330, image_box[1] + 330), "NO IMG", fill=(120, 120, 120), font=name_font)
        name = fit_text(product.get("name") or product.get("product_id") or "Sfera 新品", name_font, card_w - 36)
        draw.text((x + 24, y + image_h + 26), name, fill=(20, 20, 20), font=name_font)
    canvas.save(output, format="JPEG", quality=94)
    return str(output)


def build_category_links_message(category, products):
    lines = [f"**Sfera NUEVO｜{category}｜{len(products)} 款**", ""]
    for idx, product in enumerate(products, 1):
        name = product.get("name") or product.get("product_id") or "Sfera 新品"
        url = product.get("url") or ""
        if url:
            lines.append(f"{idx}. [{name}]({url})")
        else:
            lines.append(f"{idx}. {name}")
    return "\n".join(lines)


def group_by_category(products):
    grouped = {}
    for product in products:
        grouped.setdefault(product.get("category") or "未分类", []).append(product)
    return grouped


def send_category_image_and_links(webhook, products, state_dir):
    results = []
    for category, category_products in group_by_category(products).items():
        image_path = make_category_grid_image(category_products, state_dir, category)
        results.append(send_wecom_image(webhook, image_path))
        results.append(send_wecom(webhook, build_category_links_message(category, category_products)))
        time.sleep(0.5)
    return results


def enabled_sites(config, selected):
    available = config.get("enabled_sites") or ["sfera", "bijou", "bershka"]
    if selected != "all":
        return [selected]
    return [site for site in available if site in SITE_META]


def site_config(config, site_key):
    cfg = dict(config)
    sites = config.get("sites") or {}
    cfg.update(sites.get(site_key, {}))
    return cfg


def process_site(site_key, config, store, args):
    meta = SITE_META[site_key]
    cfg = site_config(config, site_key)
    site_url = cfg.get("base_url") or meta["base_url"]
    site_name = cfg.get("display_name") or meta["display_name"]
    marker = cfg.get("marker") or meta["marker"]
    products = scrape_site(site_key, cfg)
    new_products = []
    for product in products:
        product.setdefault("site", site_key)
        is_new_seen = store.mark_seen(product)
        if args.force_new or is_new_seen:
            new_products.append(product)
    if args.baseline_only:
        print(f"[基线] {site_name} 仅记录本次商品状态，不下载图片、不发送新增商品包。")
        new_products = []
    for product in new_products:
        refine_product_image(product)
        if config.get("download_images", True):
            try:
                product["image_path"] = download_image(product, config["state_dir"])
            except Exception as exc:
                print(f"[图片下载失败] {site_name} {product.get('name')}: {exc}")
    snapshot_path = Path(config["state_dir"]) / f"snapshot_{site_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    snapshot_path.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[汇总] {site_name} {marker} 商品 {len(products)} 个；本次新增 {len(new_products)} 个。")
    print(f"[快照] {snapshot_path}")
    if not args.baseline_only and (args.send or new_products or config.get("send_empty_report", False)):
        if new_products:
            result = send_wecom_zip_bundle(
                config["wecom_webhook"],
                new_products,
                config["state_dir"],
                site_url,
                site_name,
                marker,
            )
        else:
            result = send_wecom(
                config["wecom_webhook"],
                build_message(new_products, site_url, site_name, marker),
            )
        print(f"[企业微信] {site_name} {json.dumps(result, ensure_ascii=False)}")
    return products, new_products


def run(args):
    config = load_config()
    if args.test_wecom:
        result = send_wecom(config["wecom_webhook"], "产品上新监控：企业微信机器人测试消息发送成功。")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    store = Store(config["state_dir"])
    failures = []
    for site_key in enabled_sites(config, args.site):
        try:
            process_site(site_key, config, store, args)
        except Exception as exc:
            failures.append((site_key, exc))
            print(f"[错误] {SITE_META.get(site_key, {}).get('display_name', site_key)}: {exc}", file=sys.stderr)
    return 1 if failures and len(failures) == len(enabled_sites(config, args.site)) else 0


def main():
    parser = argparse.ArgumentParser(description="Sfera NUEVO product monitor")
    parser.add_argument("--test-wecom", action="store_true", help="send a WeCom test message only")
    parser.add_argument("--send", action="store_true", help="send report even when no new products are found")
    parser.add_argument("--force-new", action="store_true", help="treat all current monitored products as new for testing")
    parser.add_argument("--baseline-only", action="store_true", help="record current products without downloading images or sending product bundles")
    parser.add_argument("--site", choices=["sfera", "bijou", "bershka", "all"], default="all", help="site to run")
    parser.add_argument("--test-news", action="store_true", help="send one WeCom news batch with the first 8 current NUEVO products")
    parser.add_argument("--test-image", action="store_true", help="send one generated product-list image with the first 8 current NUEVO products")
    parser.add_argument("--test-upload-news", action="store_true", help="upload product images to a temporary public host and send one WeCom news batch")
    parser.add_argument("--test-batch", action="store_true", help="send one batch as generated image plus markdown links")
    parser.add_argument("--test-category-batch", action="store_true", help="send first category as one clear image grid plus links")
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
