import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import sys
import time
import zipfile
from datetime import datetime
from functools import lru_cache
from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright


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
    "lovisa": {
        "display_name": "Lovisa",
        "marker": "New",
        "base_url": "https://www.lovisa.com/collections/new-arrivals?page=1",
    },
    "stradivarius": {
        "display_name": "Stradivarius",
        "marker": "Newly appeared",
        "base_url": "https://www.stradivarius.com/gb/women/accessories/jewellery-n1883",
    },
    "primark": {
        "display_name": "Primark",
        "marker": "Newly appeared",
        "base_url": "https://www.primark.com/en-us/c/women/accessories/jewelry",
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
    if "lovisa.com" in host or "shopify" in host:
        return "https://www.lovisa.com/"
    if "stradivarius" in host:
        return "https://www.stradivarius.com/"
    if "primark.com" in host or "amplience.net" in host:
        return "https://www.primark.com/"
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
    elif site == "lovisa":
        product["image_url"] = first_white_background_image(candidates) or product.get("image_url", "")
    elif site == "stradivarius":
        product["image_url"] = first_white_background_image(candidates) or product.get("image_url", "")
    elif site == "primark":
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


def fetch_bershka_products_array_chunk(chunk, config, category_url, headers):
    store_id = config.get("store_id")
    catalog_id = config.get("catalog_id")
    language_id = config.get("language_id", -1)
    product_ids_param = quote(",".join(chunk), safe=",")
    url = (
        f"https://www.bershka.com/itxrest/3/catalog/store/{store_id}/{catalog_id}/productsArray"
        f"?languageId={quote(str(language_id))}&appId=1&productIds={product_ids_param}"
    )
    try:
        payload = fetch_json(url, headers, retries=3)
        products = payload.get("products") or extract_bershka_products_from_payload(payload)
        print(f"[Bershka] productsArray 批次 {len(chunk)} 个 ID 成功，返回 {len(products)} 个商品")
        return products
    except Exception as exc:
        if len(chunk) == 1:
            print(f"[Bershka] productsArray 单个 ID {chunk[0]} 失败，跳过：{exc}")
            return []
        mid = max(1, len(chunk) // 2)
        print(f"[Bershka] productsArray 批次 {len(chunk)} 个 ID 失败，拆分重试：{exc}")
        time.sleep(2)
        return (
            fetch_bershka_products_array_chunk(chunk[:mid], config, category_url, headers)
            + fetch_bershka_products_array_chunk(chunk[mid:], config, category_url, headers)
        )


def fetch_bershka_products_array(product_ids, config, category_url):
    if not product_ids:
        return []
    store_id = config.get("store_id")
    catalog_id = config.get("catalog_id")
    if not store_id or not catalog_id:
        return []
    products = []
    headers = bershka_headers(category_url)
    for start in range(0, len(product_ids), 10):
        chunk = product_ids[start : start + 10]
        products.extend(fetch_bershka_products_array_chunk(chunk, config, category_url, headers))
        time.sleep(2)
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
        try:
            raw_products = fetch_bershka_category_products(category if isinstance(category, dict) else {"name": category_name, "url": category_url}, config)
        except Exception as exc:
            print(f"[警告] Bershka {category_name} 本次抓取失败，跳过该分类：{exc}")
            continue
        mapped = [map_bershka_product(item, category_name, category_url, config) for item in raw_products]
        mapped = [product for product in mapped if product.get("product_id") and (product.get("name") or product.get("image_url"))]
        print(f"[结果] Bershka {category_name}: 当前商品 {len(mapped)} 个")
        products.extend(mapped)
        time.sleep(2)
    unique = {}
    for product in products:
        unique[product["product_id"]] = product
    return list(unique.values())


def lovisa_headers(referer=None):
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "referer": referer or "https://www.lovisa.com/collections/new-arrivals?page=1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }


def lovisa_products_json_url(base_url):
    parsed = urlparse(base_url or "https://www.lovisa.com/collections/new-arrivals?page=1")
    path = parsed.path.rstrip("/") or "/collections/new-arrivals"
    query = parse_qs(parsed.query)
    page = (query.get("page") or ["1"])[0]
    return urljoin("https://www.lovisa.com/", f"{path}/products.json?limit=250&page={quote(str(page))}")


def lovisa_category_from_name(name):
    text = normalize_text(name).lower()
    if "waterproof" in text:
        return "不锈钢"
    if "plated" in text:
        return "真金"
    if "cubic zirconia" in text:
        return "CZ"
    return "fashion"


def lovisa_price(product):
    variants = product.get("variants") or []
    prices = []
    for variant in variants:
        value = variant.get("price") if isinstance(variant, dict) else None
        try:
            prices.append(float(value))
        except (TypeError, ValueError):
            pass
    if prices:
        return f"${min(prices):.2f}"
    return ""


def lovisa_absolute_image_url(value):
    value = unescape(str(value or "")).strip()
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http"):
        return value
    return urljoin("https://www.lovisa.com/", value)


def lovisa_image_candidates(product):
    candidates = []
    for image in product.get("images") or []:
        if isinstance(image, dict):
            value = image.get("src") or image.get("url") or image.get("image")
        else:
            value = image
        source = lovisa_absolute_image_url(value)
        if source:
            candidates.append(source)
    for variant in product.get("variants") or []:
        image_id = variant.get("image_id") if isinstance(variant, dict) else None
        for image in product.get("images") or []:
            if isinstance(image, dict) and image_id and image.get("id") == image_id:
                source = lovisa_absolute_image_url(image.get("src") or image.get("url"))
                if source:
                    candidates.insert(0, source)
    return unique_site_urls(candidates, "https://www.lovisa.com/")


def map_lovisa_product(product, base_url):
    name = normalize_text(product.get("title") or product.get("name") or "")
    handle = normalize_text(product.get("handle") or "")
    product_id = str(product.get("id") or handle or "")
    product_url_value = urljoin("https://www.lovisa.com/", f"/products/{handle}") if handle else (base_url or "https://www.lovisa.com/collections/new-arrivals?page=1")
    candidates = lovisa_image_candidates(product)
    fallback_id = product_id_for({"url": product_url_value, "name": name, "price": lovisa_price(product)})
    return {
        "site": "lovisa",
        "category": lovisa_category_from_name(name),
        "name": name,
        "price": lovisa_price(product),
        "url": product_url_value,
        "image_url": candidates[0] if candidates else "",
        "image_candidates": candidates,
        "source_id": product_id or handle,
        "product_id": f"lovisa:{product_id or handle or fallback_id}",
        "is_new": True,
    }


def scrape_lovisa(config):
    base_url = config.get("base_url") or "https://www.lovisa.com/collections/new-arrivals?page=1"
    api_url_value = config.get("products_api_url") or lovisa_products_json_url(base_url)
    payload = fetch_json(api_url_value, lovisa_headers(base_url), retries=3)
    raw_products = payload.get("products") or []
    products = [map_lovisa_product(product, base_url) for product in raw_products]
    products = [product for product in products if product.get("product_id") and product.get("name") and product.get("image_url")]
    unique = {}
    for product in products:
        unique[product["product_id"]] = product
    products = list(unique.values())
    counts = {category: len(items) for category, items in group_by_category(products).items()}
    print(f"[结果] Lovisa New Arrivals: New {len(products)} 个；分类 {json.dumps(counts, ensure_ascii=False)}")
    return products



def stradivarius_headers(referer=None):
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-GB,en;q=0.9",
        "origin": "https://www.stradivarius.com",
        "referer": referer or "https://www.stradivarius.com/gb/women/accessories/jewellery-n1883",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }


def extract_stradivarius_category_id(category_url):
    match = re.search(r"[/-][nc](\d+)\b", category_url or "")
    return match.group(1) if match else ""


def stradivarius_products_array_url(store_id, catalog_id, category_id, language_id=-1, country="gb", version=3):
    return (
        f"https://www.stradivarius.com/itxrest/{version}/catalog/store/{store_id}/{catalog_id}/productsArray"
        f"?categoryId={quote(str(category_id))}&languageId={quote(str(language_id))}&appId=1&country={quote(country)}"
    )


def stradivarius_api_urls(config, category):
    explicit = category.get("api_url") or category.get("products_api_url")
    if explicit:
        return [explicit]
    store_id = config.get("store_id")
    catalog_id = config.get("catalog_id")
    category_id = category.get("category_id") or extract_stradivarius_category_id(category.get("url", ""))
    language_id = config.get("language_id", -1)
    country = config.get("country", "gb")
    if not store_id or not catalog_id or not category_id:
        return []
    urls = []
    urls.append(
        f"https://www.stradivarius.com/itxrest/3/catalog/store/{store_id}/{catalog_id}/category/{category_id}/product"
        f"?languageId={quote(str(language_id))}&appId=1&showProducts=false"
    )
    for version in (3,):
        urls.append(stradivarius_products_array_url(store_id, catalog_id, category_id, language_id, country, version))
        urls.append(
            f"https://www.stradivarius.com/itxrest/{version}/catalog/store/{store_id}/{catalog_id}/category/{category_id}/product"
            f"?languageId={quote(str(language_id))}&appId=1&country={quote(country)}"
        )
    return urls


def fetch_stradivarius_page(category_url, headers):
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
            "https://www.stradivarius.com/_sec/verify?provider=interstitial",
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


def discover_stradivarius_api_context(category_url, headers):
    try:
        html = fetch_stradivarius_page(category_url, headers)
    except Exception as exc:
        raise RuntimeError(f"Stradivarius 页面/API 参数自动发现失败，请在 config.json 配置 store_id/catalog_id：{exc}") from exc
    context = {}
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
        raise RuntimeError("Stradivarius 页面未暴露完整 API 参数；请在 config.json 配置 store_id/catalog_id。")
    return context


def extract_stradivarius_commercial_ids(payload):
    return extract_bershka_commercial_ids(payload)


def extract_stradivarius_products_from_payload(payload):
    return extract_bershka_products_from_payload(payload)


def fetch_stradivarius_products_array_chunk(chunk, config, category_url, headers):
    store_id = config.get("store_id")
    catalog_id = config.get("catalog_id")
    language_id = config.get("language_id", -1)
    product_ids_param = quote(",".join(chunk), safe=",")
    url = (
        f"https://www.stradivarius.com/itxrest/3/catalog/store/{store_id}/{catalog_id}/productsArray"
        f"?languageId={quote(str(language_id))}&appId=1&productIds={product_ids_param}"
    )
    try:
        payload = fetch_json(url, headers, retries=3)
        products = payload.get("products") or extract_stradivarius_products_from_payload(payload)
        print(f"[Stradivarius] productsArray 批次 {len(chunk)} 个 ID 成功，返回 {len(products)} 个商品")
        return products
    except Exception as exc:
        if len(chunk) == 1:
            print(f"[Stradivarius] productsArray 单个 ID {chunk[0]} 失败，跳过：{exc}")
            return []
        mid = max(1, len(chunk) // 2)
        print(f"[Stradivarius] productsArray 批次 {len(chunk)} 个 ID 失败，拆分重试：{exc}")
        time.sleep(2)
        return (
            fetch_stradivarius_products_array_chunk(chunk[:mid], config, category_url, headers)
            + fetch_stradivarius_products_array_chunk(chunk[mid:], config, category_url, headers)
        )


def fetch_stradivarius_products_array(product_ids, config, category_url):
    if not product_ids:
        return []
    store_id = config.get("store_id")
    catalog_id = config.get("catalog_id")
    if not store_id or not catalog_id:
        return []
    products = []
    headers = stradivarius_headers(category_url)
    for start in range(0, len(product_ids), 10):
        chunk = product_ids[start : start + 10]
        products.extend(fetch_stradivarius_products_array_chunk(chunk, config, category_url, headers))
        time.sleep(2)
    return products


def stradivarius_price(item):
    return bershka_price(item).replace("£", "") if isinstance(bershka_price(item), str) and bershka_price(item).startswith("£") else bershka_price(item)


def stradivarius_absolute_url(value, base_url="https://www.stradivarius.com/gb/"):
    return bershka_absolute_url(value, base_url)


def stradivarius_image_from_media(media):
    return bershka_image_from_media(media)


def stradivarius_image_candidates(item):
    candidates = []
    for key in ("image", "imageUrl", "mainImage", "thumbnail", "url"):
        value = item.get(key) if isinstance(item, dict) else None
        if isinstance(value, str) and re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)|/photos/|/assets/|/static/|/photos/", value, re.I):
            candidates.append(stradivarius_absolute_url(value))
        elif isinstance(value, dict):
            candidate = stradivarius_image_from_media(value)
            if candidate:
                candidates.append(candidate)
    for value in iter_nested(item):
        if isinstance(value, dict):
            candidate = stradivarius_image_from_media(value)
            if candidate and re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)|/photos/|/assets/|/static/", candidate, re.I):
                candidates.append(candidate)
        elif isinstance(value, str) and re.search(r"https?://[^\s\"']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\"']*)?$", value, re.I):
            candidates.append(value)
    return unique_site_urls(candidates, "https://www.stradivarius.com/gb/")


def stradivarius_product_url(item, category_url):
    for key in ("productUrl", "detailUrl", "seoUrl", "url", "href"):
        value = first_value(item, [key])
        if isinstance(value, str) and value:
            if re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", value, re.I):
                continue
            return stradivarius_absolute_url(value, "https://www.stradivarius.com/gb/")
    product_id = str(first_value(item, ["id", "productId", "product_id", "bundleProductId"]) or "")
    if product_id:
        return re.sub(r"\.html(?:\?.*)?$", f"p{product_id}.html", category_url)
    return category_url


def map_stradivarius_product(item, category_name, category_url, config):
    source_id = str(first_value(item, ["id", "productId", "product_id", "bundleProductId"]) or "")
    name = normalize_text(str(first_value(item, ["name", "title", "productName", "displayName", "nameEn"]) or find_first_nested(item, ["name", "title", "nameEn"]) or ""))
    product_url_value = stradivarius_product_url(item, category_url)
    if not source_id:
        match = re.search(r"p(\d+)\.html", product_url_value)
        source_id = match.group(1) if match else ""
    fallback_id = product_id_for({"url": product_url_value, "category": category_name, "name": name, "price": stradivarius_price(item)})
    candidates = stradivarius_image_candidates(item)
    return {
        "site": "stradivarius",
        "category": category_name,
        "name": name,
        "price": stradivarius_price(item),
        "url": product_url_value,
        "image_url": candidates[0] if candidates else "",
        "image_candidates": candidates,
        "source_id": source_id,
        "product_id": f"stradivarius:{source_id or fallback_id}",
        "is_new": True,
    }


def fetch_stradivarius_category_products(category, config):
    category_name = category.get("name") or category.get("category_id") or "unknown"
    category_url = category.get("url") or config.get("base_url") or "https://www.stradivarius.com/gb/women/accessories/jewellery-n1883"
    headers = stradivarius_headers(category_url)
    urls = stradivarius_api_urls(config, category)
    active_config = config
    if not urls:
        discovered = discover_stradivarius_api_context(category_url, headers)
        active_config = dict(config)
        active_config.update({key: value for key, value in discovered.items() if key in ("store_id", "catalog_id", "language_id")})
        urls = stradivarius_api_urls(active_config, category)
    errors = []
    for url in urls:
        try:
            print(f"[Stradivarius] 请求 {category_name}: {url}")
            payload = fetch_json(url, headers, retries=3)
            products = extract_stradivarius_products_from_payload(payload)
            if products:
                print(f"[Stradivarius] {category_name}: 直接解析商品 {len(products)} 个")
                return products
            product_ids = extract_stradivarius_commercial_ids(payload)
            if product_ids:
                print(f"[Stradivarius] {category_name}: 解析到商品 ID {len(product_ids)} 个，继续请求 productsArray")
                time.sleep(2)
                products = fetch_stradivarius_products_array(product_ids, active_config, category_url)
                if products:
                    print(f"[Stradivarius] {category_name}: productsArray 返回商品 {len(products)} 个")
                    return products
                errors.append(f"{url}: 解析到 {len(product_ids)} 个商品 ID，但 productsArray 未返回商品")
                continue
            errors.append(f"{url}: 返回 JSON 但未解析到商品或商品 ID")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("Stradivarius 分类抓取失败：" + "；".join(errors))


def scrape_stradivarius(config):
    products = []
    categories = config.get("categories") or []
    if not categories:
        raise RuntimeError("Stradivarius 未配置 categories")
    for category in categories:
        category_name = category.get("name") if isinstance(category, dict) else str(category)
        category_url = category.get("url") if isinstance(category, dict) else config.get("base_url", "https://www.stradivarius.com/gb/women/accessories/jewellery-n1883")
        category_id = category.get("category_id") or extract_stradivarius_category_id(category_url) if isinstance(category, dict) else ""
        print(f"[抓取] Stradivarius {category_name} {category_id}")
        try:
            raw_products = fetch_stradivarius_category_products(category if isinstance(category, dict) else {"name": category_name, "url": category_url}, config)
        except Exception as exc:
            print(f"[警告] Stradivarius {category_name} 本次抓取失败，跳过该分类：{exc}")
            continue
        mapped = [map_stradivarius_product(item, category_name, category_url, config) for item in raw_products]
        mapped = [product for product in mapped if product.get("product_id") and (product.get("name") or product.get("image_url"))]
        print(f"[结果] Stradivarius {category_name}: 当前商品 {len(mapped)} 个")
        products.extend(mapped)
        time.sleep(2)
    unique = {}
    for product in products:
        unique[product["product_id"]] = product
    return list(unique.values())



def primark_profile_dir(config):
    state_dir = Path(config.get("state_dir") or Path(__file__).with_name("state"))
    profile = state_dir / "chrome_profile_primark"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def primark_browser_channel(config):
    channel = config.get("browser_channel")
    if channel is not None:
        channel = str(channel).strip()
        return channel or None
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return None
    return "chrome"


def primark_launch_persistent_context(playwright, config, headless):
    kwargs = {
        "user_data_dir": str(primark_profile_dir(config)),
        "headless": headless,
        "locale": "en-US",
        "viewport": {"width": 1440, "height": 1000},
        "slow_mo": int(config.get("slow_mo_ms", 0) or 0),
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    channel = primark_browser_channel(config)
    if channel:
        kwargs["channel"] = channel
    return playwright.chromium.launch_persistent_context(**kwargs)


def primark_open_context(config):
    playwright = sync_playwright().start()
    return primark_launch_persistent_context(playwright, config, bool(config.get("headless", False)))


def primark_headers(user_agent="", language="en-US", referer="https://www.primark.com/"):
    return {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "accept-language": f"{language},en;q=0.9",
        "cache-control": "max-age=0",
        "priority": "u=0, i",
        "referer": referer,
        "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    }


def primark_cookie_header(cookies):
    pairs = []
    for cookie in cookies:
        domain = (cookie.get("domain") or "").lower()
        if "primark.com" not in domain:
            continue
        pairs.append(f"{cookie.get('name')}={cookie.get('value')}")
    return "; ".join(pairs)


def primark_backend_page_url(base_url, page_no):
    if page_no <= 1:
        return base_url
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)
    query["page"] = [str(page_no)]
    encoded = urlencode(query, doseq=True)
    return parsed._replace(query=encoded).geturl()


def primark_html_is_challenge(html_text):
    text = (html_text or "").lower()
    return "challenge validation" in text or "sec-cpt-if" in text or "provider=\"crypto\"" in text


def primark_backend_session(config, base_url):
    preferred_headless = bool(config.get("backend_headless", True))
    modes = [preferred_headless]
    if False not in modes:
        modes.append(False)
    last_error = None
    for headless in modes:
        playwright = sync_playwright().start()
        context = primark_launch_persistent_context(playwright, config, headless)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(base_url, wait_until="domcontentloaded", timeout=90000)
            primark_wait_and_accept_cookies(page)
            page.wait_for_timeout(6000)
            html_text = page.content()
            if primark_html_is_challenge(html_text):
                raise RuntimeError(f"Primark backend session 页面仍返回 challenge（headless={headless}）")
            if not primark_ldjson_products(html_text):
                raise RuntimeError(f"Primark backend session 页面未提取到 ld+json 商品（headless={headless}）")
            session = {
                "cookies": primark_cookie_header(context.cookies()),
                "user_agent": page.evaluate("() => navigator.userAgent"),
                "language": page.evaluate("() => navigator.language") or "en-US",
                "playwright": playwright,
                "context": context,
                "page": page,
                "headless": headless,
            }
            print(f"[Primark][backend] 会话建立成功（headless={headless}）")
            return session
        except Exception as exc:
            last_error = exc
            print(f"[Primark][backend] 会话建立失败（headless={headless}）：{exc}")
            context.close()
            playwright.stop()
    raise last_error or RuntimeError("Primark backend session 建立失败")


def primark_close_backend_session(session):
    page = session.get("page")
    context = session.get("context")
    playwright = session.get("playwright")
    if page:
        try:
            page.close()
        except Exception:
            pass
    if context:
        context.close()
    if playwright:
        playwright.stop()


def primark_fetch_html(url, session, referer):
    headers = primark_headers(session.get("user_agent") or "", session.get("language") or "en-US", referer)
    if session.get("cookies"):
        headers["cookie"] = session["cookies"]
    try:
        html_text = fetch_text(url, headers, retries=2)
        if primark_html_is_challenge(html_text):
            raise RuntimeError("Primark backend HTML 返回 challenge")
        return html_text
    except Exception as exc:
        page = session.get("page")
        if not page:
            raise
        print(f"[Primark][backend] 直拉 HTML 失败，改用页面内 fetch 获取：{exc}")
        html_text = page.evaluate(
            """
            async ({url, referer}) => {
                const resp = await fetch(url, {
                    method: 'GET',
                    credentials: 'include',
                    headers: {
                        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                        'x-primark-referer': referer || document.location.href,
                    },
                });
                return await resp.text();
            }
            """,
            {"url": url, "referer": referer},
        )
        if not primark_html_is_challenge(html_text):
            return html_text
        print("[Primark][backend] 页面内 fetch 仍返回 challenge，改用同页导航获取")
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2000)
        html_text = page.content()
        if primark_html_is_challenge(html_text):
            raise RuntimeError("Primark backend 同页导航后仍返回 challenge")
        return html_text


def primark_ldjson_products(html_text):
    for match in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.I | re.S):
        payload = normalize_text(match.group(1))
        if "ItemList" not in payload:
            continue
        try:
            data = json.loads(unescape(match.group(1)))
        except Exception:
            continue
        items = data.get("itemListElement") or []
        if items:
            return items
    return []


def primark_products_from_html(html_text):
    products = []
    for row in primark_ldjson_products(html_text):
        item = row.get("item") or {}
        offers = item.get("offers") or {}
        price = offers.get("price") or offers.get("lowPrice") or offers.get("highPrice") or ""
        image = item.get("image") or ""
        products.append(
            {
                "url": item.get("url") or "",
                "name": normalize_text(item.get("name") or ""),
                "price": f"${price}" if price and not str(price).startswith("$") else str(price or ""),
                "image_url": image,
                "image_candidates": [image] if image else [],
                "source_id": item.get("sku") or "",
            }
        )
    return products


def scrape_primark_via_backend(config):
    base_url = config.get("base_url") or "https://www.primark.com/en-us/c/women/accessories/jewelry"
    session = primark_backend_session(config, base_url)
    products_by_url = {}
    try:
        for page_no in range(1, int(config.get("max_scroll_rounds", 12) or 12) + 1):
            page_url = primark_backend_page_url(base_url, page_no)
            try:
                html_text = primark_fetch_html(page_url, session, base_url)
            except HTTPError as exc:
                if exc.code == 404 and page_no > 1:
                    print(f"[Primark][backend] 第 {page_no} 页返回 404，视为分页结束")
                    break
                raise
            if primark_html_is_challenge(html_text):
                raise RuntimeError(f"Primark backend 第 {page_no} 页返回 challenge")
            raw_products = primark_products_from_html(html_text)
            print(f"[Primark][backend] 第 {page_no} 页商品 {len(raw_products)} 个；累计 {len(products_by_url) + len([p for p in raw_products if p.get('url') and p.get('url') not in products_by_url])} 个")
            if not raw_products:
                if page_no == 1:
                    raise RuntimeError("Primark backend HTML 未提取到商品")
                break
            added = 0
            for product in raw_products:
                if product.get("url") and product["url"] not in products_by_url:
                    products_by_url[product["url"]] = product
                    added += 1
            if added == 0:
                break
        if not products_by_url:
            raise RuntimeError("Primark backend HTML 未提取到商品")
        mapped = [map_primark_product(product) for product in products_by_url.values()]
        mapped = [product for product in mapped if product.get("product_id") and product.get("name") and product.get("image_url")]
        print(f"[Primark][backend] 成功提取 {len(mapped)} 个商品")
        return mapped
    finally:
        primark_close_backend_session(session)


def primark_wait_and_accept_cookies(page):
    page.wait_for_timeout(8000)
    for text in ["ACCEPT ALL COOKIES", "Accept All Cookies", "ONLY REQUIRED COOKIES", "Only Required Cookies"]:
        try:
            btn = page.get_by_text(text, exact=False).first
            if btn.count() and btn.is_visible(timeout=1000):
                btn.click(timeout=3000)
                page.wait_for_timeout(1500)
                break
        except Exception:
            pass


def primark_load_more_href(page):
    try:
        link = page.locator('a[data-testautomation-id="load-more-button"]').first
        if link.count():
            href = link.get_attribute("href")
            if href:
                return href
    except Exception:
        pass
    return ""


def primark_listing_products(page):
    rows = page.locator('a[href*="/p/"]').evaluate_all(
        r"""
        els => {
          const seen = new Set();
          const rows = [];
          for (const a of els) {
            const href = a.href;
            if (!href || seen.has(href)) continue;
            seen.add(href);
            const card = a.closest('article, li, div');
            const img = (card ? card.querySelector('img') : a.querySelector('img'));
            const text = (card ? (card.innerText || '') : (a.innerText || '')).replace(/\s+/g,' ').trim();
            rows.push({
              href,
              title: (img && img.alt) ? img.alt.trim() : '',
              text,
              image: img ? (img.currentSrc || img.src || '') : '',
            });
          }
          return rows;
        }
        """
    )
    products = []
    for row in rows:
        title = normalize_text(row.get("title") or "")
        text = normalize_text(row.get("text") or "")
        price_match = re.search(r"\$\d+(?:\.\d{2})?", text)
        products.append(
            {
                "url": row.get("href") or "",
                "name": title,
                "price": price_match.group(0) if price_match else "",
                "image_url": row.get("image") or "",
                "image_candidates": [row.get("image")] if row.get("image") else [],
            }
        )
    return products


def primark_detail_image_candidates(context, product_url):
    page = context.new_page()
    try:
        page.goto(product_url, wait_until="domcontentloaded", timeout=90000)
        primark_wait_and_accept_cookies(page)
        candidates = page.locator("img").evaluate_all(
            r"""
            els => els
              .map(e => ({src: e.currentSrc || e.src || '', alt: e.alt || '', w: e.naturalWidth || 0, h: e.naturalHeight || 0}))
              .filter(x => x.src && x.alt && x.w >= 300 && x.h >= 300)
              .map(x => x.src)
            """
        )
        return unique_site_urls(candidates, "https://www.primark.com/")
    except Exception:
        return []
    finally:
        page.close()


def primark_image_candidates(product):
    return unique_site_urls(product.get("image_candidates") or [product.get("image_url")], "https://www.primark.com/")


def primark_enrich_detail_images(config, products):
    pending = [product for product in products if not has_white_background(product.get("image_url") or "")]
    if pending:
        print(f"[Primark] 列表首图非白底，补抓详情页 {len(pending)} 个")
    playwright = sync_playwright().start()
    context = primark_launch_persistent_context(playwright, config, bool(config.get("headless", False)))
    try:
        for index, product in enumerate(pending, start=1):
            detail_candidates = primark_detail_image_candidates(context, product.get("url") or "")
            if detail_candidates:
                combined = unique_site_urls((product.get("image_candidates") or []) + detail_candidates, "https://www.primark.com/")
                product["image_candidates"] = combined
                product["image_url"] = combined[0]
            print(f"[Primark] 详情补图 {index}/{len(pending)}")
            time.sleep(1)
    finally:
        context.close()
        playwright.stop()
    return products


def map_primark_product(product):
    name = normalize_text(product.get("name") or "")
    product_url_value = product.get("url") or "https://www.primark.com/en-us/c/women/accessories/jewelry"
    match = re.search(r"-(\d+)$", urlparse(product_url_value).path.rstrip("/"))
    source_id = match.group(1) if match else ""
    fallback_id = product_id_for({"url": product_url_value, "name": name, "price": product.get("price") or ""})
    candidates = primark_image_candidates(product)
    return {
        "site": "primark",
        "category": "JEWELRY",
        "name": name,
        "price": product.get("price") or "",
        "url": product_url_value,
        "image_url": candidates[0] if candidates else (product.get("image_url") or ""),
        "image_candidates": candidates,
        "source_id": source_id,
        "product_id": f"primark:{source_id or fallback_id}",
        "is_new": True,
    }


def scrape_primark_via_browser(config):
    base_url = config.get("base_url") or "https://www.primark.com/en-us/c/women/accessories/jewelry"
    browser_headless = bool(config.get("browser_headless", config.get("headless", False)))
    playwright = sync_playwright().start()
    context = primark_launch_persistent_context(playwright, config, browser_headless)
    try:
        products_by_url = {}
        next_url = base_url
        page_no = 1
        while next_url:
            page = context.pages[0] if page_no == 1 and context.pages else context.new_page()
            page.goto(next_url, wait_until="domcontentloaded", timeout=90000)
            primark_wait_and_accept_cookies(page)
            page.wait_for_timeout(2000)
            raw_products = primark_listing_products(page)
            for product in raw_products:
                if product.get("url"):
                    products_by_url[product["url"]] = product
            print(f"[Primark][browser] 第 {page_no} 页商品 {len(raw_products)} 个；累计 {len(products_by_url)} 个")
            href = primark_load_more_href(page)
            next_url = urljoin("https://www.primark.com/", href) if href else ""
            if page_no > 1:
                page.close()
            page_no += 1
            if page_no > int(config.get("max_scroll_rounds", 12) or 12):
                break
        mapped = [map_primark_product(product) for product in products_by_url.values()]
        mapped = [product for product in mapped if product.get("product_id") and product.get("name") and product.get("image_url")]
        unique = {}
        for product in mapped:
            unique[product["product_id"]] = product
        products = list(unique.values())
        print(f"[Primark][browser] 成功提取 {len(products)} 个商品")
        return products
    finally:
        context.close()
        playwright.stop()


def scrape_primark(config):
    prefer_backend = config.get("prefer_backend", True)
    fallback_to_browser = config.get("fallback_to_browser", False)
    if prefer_backend:
        try:
            products = scrape_primark_via_backend(config)
            print(f"[结果] Primark Jewelry: 当前商品 {len(products)} 个（backend）")
            return products
        except Exception as exc:
            if not fallback_to_browser:
                raise RuntimeError(f"Primark backend 抓取失败：{exc}") from exc
            print(f"[Primark][backend] 失败，回退到列表页浏览器方案：{exc}")
    products = scrape_primark_via_browser(config)
    print(f"[结果] Primark Jewelry: 当前商品 {len(products)} 个（browser-listing-only）")
    return products



def scrape_site(site_key, config):
    if site_key == "sfera":
        return scrape_sfera(config)
    if site_key == "bijou":
        return scrape_bijou(config)
    if site_key == "bershka":
        return scrape_bershka(config)
    if site_key == "lovisa":
        return scrape_lovisa(config)
    if site_key == "stradivarius":
        return scrape_stradivarius(config)
    if site_key == "primark":
        return scrape_primark(config)
    raise ValueError(f"未知站点：{site_key}")


def wecom_retryable_error(exc):
    text = str(exc).lower()
    return isinstance(exc, (TimeoutError, socket.timeout, ssl.SSLError, OSError)) or "eof occurred in violation of protocol" in text or "timed out" in text or "connection reset" in text


def post_wecom(webhook, payload, retries=3):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(webhook, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt == retries or not wecom_retryable_error(exc):
                raise
            print(f"[企业微信] 消息发送重试 {attempt}/{retries}：{exc}")
            time.sleep(2 * attempt)
    raise last_error


def send_wecom(webhook, content):
    return post_wecom(webhook, {"msgtype": "markdown", "markdown": {"content": content}})


def wecom_robot_key(webhook):
    query = parse_qs(urlparse(webhook).query)
    keys = query.get("key") or []
    if not keys or not keys[0]:
        raise RuntimeError("企业微信 webhook 缺少 key，无法上传文件")
    return keys[0]


def upload_wecom_file(webhook, file_path, retries=3):
    key = wecom_robot_key(webhook)
    path = Path(file_path)
    file_bytes = path.read_bytes()
    last_error = None
    for attempt in range(1, retries + 1):
        boundary = "----SferaWeComBoundary" + hashlib.md5(f"{time.time()}-{attempt}".encode()).hexdigest()
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"{path.name}\"\r\nContent-Type: application/zip\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
        try:
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
        except Exception as exc:
            last_error = exc
            if attempt == retries or not wecom_retryable_error(exc):
                raise
            print(f"[企业微信] 文件上传重试 {attempt}/{retries}：{exc}")
            time.sleep(2 * attempt)
    raise last_error


def send_wecom_file(webhook, file_path):
    media_id = upload_wecom_file(webhook, file_path)
    return post_wecom(webhook, {"msgtype": "file", "file": {"media_id": media_id}})


def safe_filename(value, fallback="product"):
    name = normalize_text(value) or fallback
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(" ._")
    return name[:90] or fallback


def product_image_filename_base(product, index):
    name = safe_filename(product.get("name") or product.get("product_id") or f"product_{index}")
    if product.get("site") != "bijou":
        return name
    product_number = safe_filename(product.get("source_id") or "", "").strip("_")
    if not product_number:
        return name
    if name == product_number or name.startswith(f"{product_number}_"):
        return name
    return safe_filename(f"{product_number}_{name}")


def save_product_image_as_jpg(product, target_dir, index):
    source_path = product.get("image_path") or download_image(product, target_dir)
    if not source_path:
        return None
    target_dir = Path(target_dir)
    base = product_image_filename_base(product, index)
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


def prepare_zip_images(products, state_dir, site_name="Sfera"):
    bundle_root = Path(state_dir) / "wecom_zips" / f"{safe_filename(site_name, 'site').replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    image_dir = bundle_root / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    prepared = []
    for index, product in enumerate(products, 1):
        try:
            image_path = save_product_image_as_jpg(product, image_dir, index)
            if image_path:
                prepared.append({"product": product, "image_path": image_path, "size": image_path.stat().st_size})
        except Exception as exc:
            print(f"[图片保存失败] {product.get('name')}: {exc}")
    return bundle_root, image_dir, prepared


def write_zip_from_paths(zip_path, image_paths):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for image_path in image_paths:
            zf.write(image_path, image_path.name)
    return zip_path


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
    return master_zip, category_zips, category_counts, bundle_root


def split_zip_bundle_by_size(products, state_dir, site_url, site_name="Sfera", marker="NUEVO", max_bytes=19 * 1024 * 1024):
    if not products:
        return [], [], None
    bundle_root, image_dir, prepared = prepare_zip_images(products, state_dir, site_name)
    if not prepared:
        return [], [], bundle_root
    bundles = []
    current_bundle = []
    site_slug = safe_filename(site_name, 'site').replace(' ', '_')
    trial_zip = bundle_root / f"{site_slug}_trial.zip"
    for item in prepared:
        candidate_bundle = current_bundle + [item]
        write_zip_from_paths(trial_zip, [row["image_path"] for row in candidate_bundle])
        trial_size = trial_zip.stat().st_size if trial_zip.exists() else 0
        if current_bundle and trial_size > max_bytes:
            bundles.append(list(current_bundle))
            current_bundle = [item]
        else:
            current_bundle = candidate_bundle
    if trial_zip.exists():
        trial_zip.unlink()
    if current_bundle:
        bundles.append(list(current_bundle))
    split_zips = []
    category_counts = []
    for bundle_index, bundle_items in enumerate(bundles, 1):
        bundle_count = len(bundle_items)
        category_counts.append(("JEWELRY", len(products)))
        zip_path = bundle_root / f"{site_slug}_网站上新_{bundle_count}款_{marker}_第{bundle_index}包.zip"
        write_zip_from_paths(zip_path, [item["image_path"] for item in bundle_items])
        split_zips.append(zip_path)
    return split_zips, category_counts, bundle_root


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
    seen = set()
    for category, count in category_counts:
        key = (category, count)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {category}：{count} 款")
    lines.extend(
        [
            "",
            "下面发送的是压缩包；如果总包超过企业微信大小限制，会按总包自动拆分为多个附件发送，但品类统计保持不变。每张图片已转换为标准 JPG，并以产品名称命名。",
        ]
    )
    return "\n".join(lines)


def send_wecom_zip_bundle(webhook, products, state_dir, site_url, site_name="Sfera", marker="NUEVO"):
    max_bytes = 19 * 1024 * 1024
    master_zip, category_zips, category_counts, bundle_root = build_product_zip_bundle(products, state_dir, site_url, site_name, marker)
    zip_targets = []
    cleanup_roots = [bundle_root]
    if master_zip.stat().st_size <= max_bytes:
        zip_targets = [master_zip]
    else:
        shutil.rmtree(bundle_root, ignore_errors=True)
        zip_targets, category_counts, split_root = split_zip_bundle_by_size(products, state_dir, site_url, site_name, marker, max_bytes=max_bytes)
        cleanup_roots = [split_root] if split_root else []
    message = build_zip_bundle_message(products, category_counts, site_url, site_name, marker)
    text_result = send_wecom(webhook, message)
    sent_files = []
    for zip_target in zip_targets:
        file_result = send_wecom_file(webhook, zip_target)
        sent_files.append({"path": str(zip_target), "result": file_result})
    ok = text_result.get("errcode") == 0 and all(item["result"].get("errcode") == 0 for item in sent_files)
    if ok:
        for cleanup_root in cleanup_roots:
            shutil.rmtree(cleanup_root, ignore_errors=True)
        cleanup = "deleted"
    else:
        cleanup = "kept"
    return {"message": text_result, "files": sent_files, "zip": str(zip_targets[0]) if zip_targets else str(master_zip), "cleanup": cleanup}


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
    available = config.get("enabled_sites") or ["sfera", "bijou", "bershka", "lovisa", "stradivarius", "primark"]
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
    if site_key == "primark" and not args.baseline_only and new_products:
        print("[Primark] 当前只走后台抓取，不再打开详情页逐个补图")
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
    parser.add_argument("--site", choices=["sfera", "bijou", "bershka", "lovisa", "stradivarius", "primark", "all"], default="all", help="site to run")
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
