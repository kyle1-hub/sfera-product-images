import argparse
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sfera_monitor", ROOT / "sfera_monitor.py")
MONITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)


class BijouDeliveryTests(unittest.TestCase):
    def test_variant_number_matches_base_image_id(self):
        html = '''
        <img data-src="/media/aa/bb/142749660_0.webp"
             data-srcset="/thumbnail/aa/bb/142749660_0_400x400.webp 400w">
        '''
        candidates = MONITOR.bijou_image_candidates_from_html(html, "142749660.1")
        self.assertEqual(MONITOR.bijou_base_product_number("142749660.1"), "142749660")
        self.assertTrue(any("142749660_0.webp" in value for value in candidates))

    def test_legacy_migration_only_recovers_sunset_gem(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "sfera_products.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE products (
                    product_id TEXT PRIMARY KEY, name TEXT, price TEXT, url TEXT,
                    image_url TEXT, category TEXT, first_seen TEXT,
                    last_seen TEXT, image_path TEXT, site TEXT
                )
                """
            )
            for product_id in ("bijou:142749660.1", "bijou:other"):
                conn.execute(
                    """
                    INSERT INTO products(product_id, name, first_seen, last_seen, site)
                    VALUES (?, ?, '2026-07-19T04:36:02', '2026-07-19T04:36:02', 'bijou')
                    """,
                    (product_id, product_id),
                )
            conn.commit()
            conn.close()

            store = MONITOR.Store(temp_dir)
            rows = {
                row[0]: row[1:]
                for row in store.conn.execute(
                    "SELECT product_id, text_sent_at, image_sent_at, recovery_tag FROM products"
                )
            }
            self.assertEqual(rows["bijou:142749660.1"], (None, None, "sunset-gem-20260719"))
            self.assertEqual(rows["bijou:other"][:2], ("2026-07-19T04:36:02", "2026-07-19T04:36:02"))
            store.conn.close()

    def test_text_sends_once_and_image_retries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MONITOR.Store(temp_dir)
            product = {
                "site": "bijou",
                "category": "Neuer Schmuck",
                "name": "Ring Set - Sunset Gem",
                "price": "12,95 €",
                "url": "https://www.bijou-brigitte.com/ring-set-sunset-gem-142749660.1",
                "image_url": "",
                "image_candidates": [],
                "source_id": "142749660.1",
                "product_id": "bijou:142749660.1",
            }
            args = argparse.Namespace(baseline_only=False)
            config = {"wecom_webhook": "test", "state_dir": temp_dir}

            with patch.object(MONITOR, "send_wecom", return_value={"errcode": 0}), \
                 patch.object(MONITOR, "bijou_detail_image_candidates", return_value=[]), \
                 patch.object(MONITOR, "download_image", return_value=None), \
                 patch.object(MONITOR, "send_wecom_file") as send_file:
                MONITOR.process_bijou(config, store, args, [product], product["url"], "Bijou Brigitte", "Neu")
                MONITOR.process_bijou(config, store, args, [product], product["url"], "Bijou Brigitte", "Neu")
                self.assertEqual(MONITOR.send_wecom.call_count, 1)
                send_file.assert_not_called()

            text_sent_at, image_sent_at = store.conn.execute(
                "SELECT text_sent_at, image_sent_at FROM products WHERE product_id = ?",
                (product["product_id"],),
            ).fetchone()
            self.assertIsNotNone(text_sent_at)
            self.assertIsNone(image_sent_at)
            store.conn.close()

    def test_filename_keeps_variant_product_number(self):
        value = MONITOR.product_image_filename_base(
            {"site": "bijou", "source_id": "142749660.1", "name": "Ring Set - Sunset Gem"},
            1,
        )
        self.assertEqual(value, "142749660.1_Ring Set - Sunset Gem")

    def test_bijou_page_url_preserves_sorted_listing_query(self):
        url = "https://www.bijou-brigitte.com/schmuck/ohrringe/?order=neueste&p=1"
        self.assertEqual(MONITOR.bijou_page_url(url, 1), url)
        self.assertEqual(
            MONITOR.bijou_page_url(url, 3),
            "https://www.bijou-brigitte.com/schmuck/ohrringe/?order=neueste&p=3",
        )

    def test_extract_bijou_products_accepts_flexible_new_flag(self):
        html = '''
        <div class="cms-listing-col extra">
          <div class="product-box" data-number="142749661.1" data-group="Schmuck;Ringe" data-name="Neu Ring" data-price="12.95">
            <span class="new flag badge"><span>Neu</span></span>
            <a href="/neu-ring-142749661.1">Neu Ring</a>
            <img data-src="/media/aa/bb/142749661_0.webp">
          </div>
        </div>
        '''
        products = MONITOR.extract_bijou_products(html)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["product_id"], "bijou:142749661.1")
        self.assertTrue(products[0]["image_candidates"])

    def test_extract_bijou_products_rejects_non_new_when_flag_required(self):
        html = '''
        <div class="cms-listing-col">
          <div class="product-box" data-number="142749662.1" data-name="Old Ring" data-price="9.95">
            <a href="/old-ring-142749662.1">Old Ring</a>
          </div>
        </div>
        '''
        self.assertEqual(MONITOR.extract_bijou_products(html), [])
        self.assertEqual(len(MONITOR.extract_bijou_products(html, require_new_flag=False)), 1)

    def test_extract_bijou_products_falls_back_to_url_sku(self):
        html = '''
        <div class="cms-listing-col">
          <div class="product-box" data-name="URL Ring" data-price="7.95">
            <span class="flag new">Neu</span>
            <a href="/url-ring-142749663.1">URL Ring</a>
          </div>
        </div>
        '''
        product = MONITOR.extract_bijou_products(html)[0]
        self.assertEqual(product["source_id"], "142749663")
        self.assertEqual(product["product_id"], "bijou:142749663")

    def test_bijou_default_listing_uses_neu_without_flag_filter(self):
        listings = MONITOR.bijou_listing_configs({"base_url": "https://www.bijou-brigitte.com/neu/"})
        self.assertEqual(listings, [{"name": "Neu", "url": "https://www.bijou-brigitte.com/neu/", "require_new_flag": False}])

    def test_scrape_bijou_multiple_listings_dedupes_and_merges_images(self):
        html_one = '''
        <div class="cms-listing-col"><div class="product-box" data-number="142749664.1" data-name="Merge Ring" data-price="1.95">
          <span class="flag new">Neu</span><a href="/merge-ring-142749664.1">Merge Ring</a>
          <img data-src="/media/aa/bb/142749664_0.webp">
        </div></div>
        '''
        html_two = '''
        <div class="cms-listing-col"><div class="product-box" data-number="142749664.1" data-name="Merge Ring" data-price="1.95">
          <a href="/merge-ring-142749664.1">Merge Ring</a>
          <img data-src="/media/aa/bb/142749664_1.webp">
        </div></div>
        '''
        config = {
            "base_url": "https://www.bijou-brigitte.com/neu/",
            "listing_urls": [
                {"name": "Neu", "url": "https://www.bijou-brigitte.com/neu/", "require_new_flag": True},
                {"name": "Sorted", "url": "https://www.bijou-brigitte.com/schmuck/ohrringe/?order=neueste&p=1", "require_new_flag": False},
            ],
        }
        responses = {
            "https://www.bijou-brigitte.com/neu/": html_one,
            "https://www.bijou-brigitte.com/schmuck/ohrringe/?order=neueste&p=1": html_two,
        }
        with patch.object(MONITOR, "fetch_text", side_effect=lambda url, headers: responses[url]):
            products = MONITOR.scrape_bijou(config)
        self.assertEqual(len(products), 1)
        self.assertEqual(len(products[0]["image_candidates"]), 2)

    def test_bijou_audit_report_counts_database_website_and_new_products(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MONITOR.Store(temp_dir)
            store.mark_seen(
                {
                    "site": "bijou",
                    "category": "Neuer Schmuck",
                    "name": "Known Ring",
                    "price": "1,00 €",
                    "url": "https://www.bijou-brigitte.com/known-142749665.1",
                    "image_url": "https://www.bijou-brigitte.com/media/142749665_0.webp",
                    "image_candidates": ["https://www.bijou-brigitte.com/media/142749665_0.webp"],
                    "source_id": "142749665.1",
                    "product_id": "bijou:142749665.1",
                    "image_path": str(Path(temp_dir) / "known.jpg"),
                }
            )
            products = [
                {
                    "site": "bijou",
                    "product_id": "bijou:142749665.1",
                    "image_url": "https://www.bijou-brigitte.com/media/142749665_0.webp",
                    "image_candidates": ["https://www.bijou-brigitte.com/media/142749665_0.webp"],
                },
                {
                    "site": "bijou",
                    "product_id": "bijou:142749666.1",
                    "image_url": "https://www.bijou-brigitte.com/media/142749666_0.webp",
                    "image_candidates": ["https://www.bijou-brigitte.com/media/142749666_0.webp"],
                },
            ]
            report = MONITOR.build_bijou_audit_report(store, products)
            self.assertEqual(report["db_products"], 1)
            self.assertEqual(report["db_images"], 1)
            self.assertEqual(report["website_products"], 2)
            self.assertEqual(report["website_products_with_images"], 2)
            self.assertEqual(report["new_products"], 1)
            self.assertEqual(report["new_products_with_images"], 1)
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
