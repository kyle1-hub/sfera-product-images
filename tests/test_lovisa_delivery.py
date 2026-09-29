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

BASE_URL = "https://www.lovisa.com/collections/new-arrivals?page=1"


def raw_product(product_id, title=None, images=None, variants=None):
    return {
        "id": product_id,
        "title": title or f"Product {product_id}",
        "handle": f"product-{product_id}",
        "images": images or [],
        "variants": variants or [{"price": "12.00"}],
    }


def mapped_product(product_id="1", image_url=""):
    return {
        "site": "lovisa",
        "category": "fashion",
        "name": f"Product {product_id}",
        "price": "$12.00",
        "url": f"https://www.lovisa.com/products/product-{product_id}",
        "image_url": image_url,
        "image_candidates": [image_url] if image_url else [],
        "source_id": product_id,
        "product_id": f"lovisa:{product_id}",
        "is_new": True,
    }


class LovisaDeliveryTests(unittest.TestCase):
    def test_products_json_url_uses_explicit_page(self):
        self.assertEqual(
            MONITOR.lovisa_products_json_url(BASE_URL, page=3),
            "https://www.lovisa.com/collections/new-arrivals/products.json?limit=250&page=3",
        )
        self.assertEqual(
            MONITOR.lovisa_products_json_url(
                "https://www.lovisa.com/collections/new-arrivals/products.json?limit=10&page=9", page=2
            ),
            "https://www.lovisa.com/collections/new-arrivals/products.json?limit=250&page=2",
        )

    def test_scrape_pages_until_empty_and_keeps_no_image_products(self):
        payloads = [
            {"products": [raw_product(1), raw_product(2)]},
            {"products": [raw_product(2), raw_product(3)]},
            {"products": []},
        ]
        with patch.object(MONITOR, "fetch_json", side_effect=payloads) as fetch_json:
            products = MONITOR.scrape_lovisa({"base_url": BASE_URL})
        self.assertEqual([product["product_id"] for product in products], ["lovisa:1", "lovisa:2", "lovisa:3"])
        self.assertTrue(all(product["image_url"] == "" for product in products))
        self.assertEqual(fetch_json.call_count, 3)
        self.assertIn("page=3", fetch_json.call_args_list[2].args[0])

    def test_scrape_short_page_still_requests_empty_page(self):
        with patch.object(
            MONITOR,
            "fetch_json",
            side_effect=[{"products": [raw_product(1)]}, {"products": []}],
        ) as fetch_json:
            MONITOR.scrape_lovisa({"base_url": BASE_URL})
        self.assertEqual(fetch_json.call_count, 2)

    def test_scrape_rejects_empty_first_page_and_invalid_payload(self):
        with patch.object(MONITOR, "fetch_json", return_value={"products": []}):
            with self.assertRaises(RuntimeError):
                MONITOR.scrape_lovisa({"base_url": BASE_URL})
        with patch.object(MONITOR, "fetch_json", return_value={"items": []}):
            with self.assertRaises(RuntimeError):
                MONITOR.scrape_lovisa({"base_url": BASE_URL})

    def test_scrape_rejects_repeated_page(self):
        page = {"products": [raw_product(1)]}
        with patch.object(MONITOR, "fetch_json", side_effect=[page, page]):
            with self.assertRaises(RuntimeError):
                MONITOR.scrape_lovisa({"base_url": BASE_URL})

    def test_scrape_rejects_maximum_page_overrun(self):
        with patch.object(MONITOR, "LOVISA_MAX_PAGES", 2), patch.object(
            MONITOR,
            "fetch_json",
            side_effect=[{"products": [raw_product(1)]}, {"products": [raw_product(2)]}],
        ):
            with self.assertRaises(RuntimeError):
                MONITOR.scrape_lovisa({"base_url": BASE_URL})

    def test_mapping_keeps_parent_identity_price_category_and_variant_image(self):
        product = raw_product(
            55,
            title="Waterproof Plated Cubic Zirconia Ring",
            images=[
                {"id": 1, "src": "//cdn.example/first.jpg"},
                {"id": 2, "src": "//cdn.example/variant.jpg"},
            ],
            variants=[{"price": "20.00", "image_id": 2}, {"price": "10.50", "image_id": 2}],
        )
        mapped = MONITOR.map_lovisa_product(product, BASE_URL)
        self.assertEqual(mapped["product_id"], "lovisa:55")
        self.assertEqual(mapped["category"], "不锈钢")
        self.assertEqual(mapped["price"], "$10.50")
        self.assertEqual(mapped["image_candidates"], ["https://cdn.example/first.jpg", "https://cdn.example/variant.jpg"])

    def test_lovisa_prefers_white_but_falls_back_to_first_candidate(self):
        product = mapped_product(image_url="https://cdn.example/model.jpg")
        product["image_candidates"] = ["https://cdn.example/model.jpg", "https://cdn.example/lifestyle.jpg"]
        with patch.object(MONITOR, "has_white_background", return_value=False):
            MONITOR.refine_product_image(product)
        self.assertEqual(product["image_url"], "https://cdn.example/model.jpg")

    def test_lovisa_complete_candidates_adds_detail_images(self):
        product = mapped_product(image_url="https://cdn.example/model.jpg")
        product["url"] = "https://www.lovisa.com/products/test-product"
        detail = raw_product(
            1,
            images=[
                {"id": 1, "src": "//cdn.example/model.jpg"},
                {"id": 2, "src": "//cdn.example/white.jpg"},
            ],
        )
        with patch.object(MONITOR, "fetch_json", return_value=detail) as fetch_json:
            candidates = MONITOR.lovisa_complete_image_candidates(product)
        self.assertEqual(candidates, ["https://cdn.example/model.jpg", "https://cdn.example/white.jpg"])
        self.assertTrue(fetch_json.call_args.args[0].endswith("/products/test-product.js"))

    def test_lovisa_fetch_detail_candidates_skips_existing_white_listing_candidate(self):
        complete = mapped_product("1", image_url="https://cdn.example/white.jpg")
        complete["image_candidates"] = ["https://cdn.example/white.jpg", "https://cdn.example/model.jpg"]
        incomplete = mapped_product("2", image_url="https://cdn.example/model.jpg")
        incomplete["image_candidates"] = ["https://cdn.example/model.jpg", "https://cdn.example/lifestyle.jpg"]
        with patch.object(MONITOR, "has_white_background", side_effect=lambda url: "white" in url), patch.object(
            MONITOR, "lovisa_detail_image_candidates", return_value=["https://cdn.example/detail-white.jpg"]
        ) as detail:
            MONITOR.lovisa_fetch_detail_candidates_for_pending([complete, incomplete], workers=1)
        detail.assert_called_once_with(incomplete["url"])
        self.assertEqual(complete["image_candidates"], ["https://cdn.example/white.jpg", "https://cdn.example/model.jpg"])
        self.assertEqual(
            incomplete["image_candidates"],
            ["https://cdn.example/model.jpg", "https://cdn.example/lifestyle.jpg", "https://cdn.example/detail-white.jpg"],
        )

    def test_lovisa_fetch_detail_candidates_keeps_no_image_products_eligible(self):
        product = mapped_product("2")
        with patch.object(MONITOR, "lovisa_detail_image_candidates", return_value=["https://cdn.example/detail.jpg"]):
            MONITOR.lovisa_fetch_detail_candidates_for_pending([product], workers=1)
        self.assertEqual(product["image_candidates"], ["https://cdn.example/detail.jpg"])

    def test_lovisa_text_message_splits_large_batches(self):
        products = [mapped_product(str(index)) for index in range(40)]
        for product in products:
            product["name"] = "Long Lovisa Product Name " + product["source_id"] * 20
        messages = MONITOR.split_lovisa_text_messages(products, BASE_URL, max_bytes=900)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(MONITOR.lovisa_message_size(message) <= 900 for message in messages))
        self.assertIn("第 1/", messages[0])

    def test_lovisa_image_delivery_uses_detail_white_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MONITOR.Store(temp_dir)
            args = argparse.Namespace(baseline_only=False)
            product = mapped_product(image_url="https://cdn.example/model.jpg")
            product["image_candidates"] = ["https://cdn.example/model.jpg"]
            config = {"wecom_webhook": "test", "state_dir": temp_dir, "download_images": True, "share_library_root": str(Path(temp_dir) / "library")}
            image_path = Path(temp_dir) / "ready.jpg"
            image_path.write_bytes(b"image")
            package_path = Path(temp_dir) / "package.zip"
            package_path.write_bytes(b"zip")
            with patch.object(MONITOR, "send_wecom", return_value={"errcode": 0}), patch.object(
                MONITOR, "lovisa_detail_image_candidates", return_value=["https://cdn.example/white.jpg"]
            ), patch.object(MONITOR, "has_white_background", side_effect=lambda url: "white" in url), patch.object(
                MONITOR, "download_image", return_value=str(image_path)
            ), patch.object(MONITOR, "prepare_lovisa_image_zips", return_value=(Path(temp_dir) / "bundle", [(package_path, [product])], [{"product": product, "image_path": image_path, "size": 5}])) as prepare_zips, patch.object(
                MONITOR, "send_wecom_file", return_value={"errcode": 0}
            ):
                MONITOR.process_lovisa(config, store, args, [product], BASE_URL, "Lovisa", "New")
            prepared_product = prepare_zips.call_args.args[0][0]
            self.assertEqual(prepared_product["image_url"], "https://cdn.example/white.jpg")
            store.conn.close()

    def test_legacy_rows_migrate_complete_without_changing_new_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "sfera_products.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE products (
                    product_id TEXT PRIMARY KEY, name TEXT, price TEXT, url TEXT,
                    image_url TEXT, category TEXT, first_seen TEXT, last_seen TEXT,
                    image_path TEXT, site TEXT, delivery_version INTEGER,
                    text_sent_at TEXT, image_sent_at TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO products(product_id, name, first_seen, last_seen, site) VALUES ('lovisa:old', 'Old', '2026-07-01', '2026-07-01', 'lovisa')"
            )
            conn.execute(
                """
                INSERT INTO products(product_id, name, first_seen, last_seen, site, delivery_version)
                VALUES ('lovisa:new', 'New', '2026-07-02', '2026-07-02', 'lovisa', ?)
                """,
                (MONITOR.LOVISA_DELIVERY_VERSION,),
            )
            conn.commit()
            conn.close()
            store = MONITOR.Store(temp_dir)
            rows = {
                row[0]: row[1:]
                for row in store.conn.execute(
                    "SELECT product_id, text_sent_at, image_sent_at, delivery_version FROM products ORDER BY product_id"
                )
            }
            self.assertEqual(rows["lovisa:old"], ("2026-07-01", "2026-07-01", MONITOR.LOVISA_DELIVERY_VERSION))
            self.assertEqual(rows["lovisa:new"], (None, None, MONITOR.LOVISA_DELIVERY_VERSION))
            store.conn.close()

    def test_seen_update_keeps_existing_image_when_current_scrape_has_no_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MONITOR.Store(temp_dir)
            with_image = mapped_product(image_url="https://cdn.example/old.jpg")
            no_image = mapped_product(image_url="")
            store.mark_seen(with_image, delivery_version=MONITOR.LOVISA_DELIVERY_VERSION)
            store.mark_seen(no_image, delivery_version=MONITOR.LOVISA_DELIVERY_VERSION)
            image_url = store.conn.execute(
                "SELECT image_url FROM products WHERE product_id = ?", (with_image["product_id"],)
            ).fetchone()[0]
            self.assertEqual(image_url, "https://cdn.example/old.jpg")
            store.conn.close()

    def test_baseline_marks_products_complete_without_sending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MONITOR.Store(temp_dir)
            args = argparse.Namespace(baseline_only=True)
            product = mapped_product()
            config = {"wecom_webhook": "test", "state_dir": temp_dir, "download_images": True, "share_library_root": str(Path(temp_dir) / "library")}
            with patch.object(MONITOR, "send_wecom") as send_text, patch.object(MONITOR, "send_wecom_file") as send_file:
                MONITOR.process_lovisa(config, store, args, [product], BASE_URL, "Lovisa", "New")
            send_text.assert_not_called()
            send_file.assert_not_called()
            row = store.conn.execute(
                "SELECT text_sent_at, image_sent_at FROM products WHERE product_id = ?", (product["product_id"],)
            ).fetchone()
            self.assertTrue(all(row))
            store.conn.close()

    def test_no_image_sends_text_once_and_later_only_sends_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MONITOR.Store(temp_dir)
            args = argparse.Namespace(baseline_only=False)
            config = {"wecom_webhook": "test", "state_dir": temp_dir, "download_images": True, "share_library_root": str(Path(temp_dir) / "library")}
            no_image = mapped_product()
            with patch.object(MONITOR, "send_wecom", return_value={"errcode": 0}) as send_text, patch.object(
                MONITOR, "lovisa_detail_image_candidates", return_value=[]
            ), patch.object(MONITOR, "download_image", return_value=None), patch.object(MONITOR, "send_wecom_file") as send_file:
                MONITOR.process_lovisa(config, store, args, [no_image], BASE_URL, "Lovisa", "New")
                MONITOR.process_lovisa(config, store, args, [no_image], BASE_URL, "Lovisa", "New")
            self.assertEqual(send_text.call_count, 1)
            send_file.assert_not_called()

            with_image = mapped_product(image_url="https://cdn.example/1.jpg")
            image_path = Path(temp_dir) / "ready.jpg"
            image_path.write_bytes(b"image")
            package_path = Path(temp_dir) / "package.zip"
            package_path.write_bytes(b"zip")
            prepared = [{"product": with_image, "image_path": image_path, "size": 5}]
            with patch.object(MONITOR, "send_wecom") as send_text, patch.object(
                MONITOR, "lovisa_detail_image_candidates", return_value=[]
            ), patch.object(MONITOR, "refine_product_image", side_effect=lambda product: product), patch.object(
                MONITOR, "download_image", return_value=str(image_path)
            ), patch.object(
                MONITOR, "prepare_lovisa_image_zips", return_value=(Path(temp_dir) / "bundle", [(package_path, [with_image])], prepared)
            ), patch.object(MONITOR, "send_wecom_file", return_value={"errcode": 0}) as send_file:
                MONITOR.process_lovisa(config, store, args, [with_image], BASE_URL, "Lovisa", "New")
            send_text.assert_not_called()
            send_file.assert_called_once()
            row = store.conn.execute(
                "SELECT text_sent_at, image_sent_at FROM products WHERE product_id = ?", (with_image["product_id"],)
            ).fetchone()
            self.assertTrue(all(row))
            store.conn.close()

    def test_text_failure_stays_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MONITOR.Store(temp_dir)
            args = argparse.Namespace(baseline_only=False)
            product = mapped_product()
            config = {"wecom_webhook": "test", "state_dir": temp_dir, "download_images": False, "share_library_root": str(Path(temp_dir) / "library")}
            with patch.object(MONITOR, "send_wecom", return_value={"errcode": 40001}):
                with self.assertRaises(RuntimeError):
                    MONITOR.process_lovisa(config, store, args, [product], BASE_URL, "Lovisa", "New")
            text_sent_at = store.conn.execute(
                "SELECT text_sent_at FROM products WHERE product_id = ?", (product["product_id"],)
            ).fetchone()[0]
            self.assertIsNone(text_sent_at)
            store.conn.close()

    def test_file_failure_keeps_image_pending_after_text_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MONITOR.Store(temp_dir)
            args = argparse.Namespace(baseline_only=False)
            product = mapped_product(image_url="https://cdn.example/1.jpg")
            config = {"wecom_webhook": "test", "state_dir": temp_dir, "download_images": True, "share_library_root": str(Path(temp_dir) / "library")}
            image_path = Path(temp_dir) / "ready.jpg"
            image_path.write_bytes(b"image")
            package_path = Path(temp_dir) / "package.zip"
            package_path.write_bytes(b"zip")
            prepared = [{"product": product, "image_path": image_path, "size": 5}]
            with patch.object(MONITOR, "send_wecom", return_value={"errcode": 0}), patch.object(
                MONITOR, "lovisa_detail_image_candidates", return_value=[]
            ), patch.object(MONITOR, "refine_product_image", side_effect=lambda value: value), patch.object(
                MONITOR, "download_image", return_value=str(image_path)
            ), patch.object(
                MONITOR, "prepare_lovisa_image_zips", return_value=(Path(temp_dir) / "bundle", [(package_path, [product])], prepared)
            ), patch.object(MONITOR, "send_wecom_file", return_value={"errcode": 40001}):
                with self.assertRaises(RuntimeError):
                    MONITOR.process_lovisa(config, store, args, [product], BASE_URL, "Lovisa", "New")
            row = store.conn.execute(
                "SELECT text_sent_at, image_sent_at FROM products WHERE product_id = ?", (product["product_id"],)
            ).fetchone()
            self.assertIsNotNone(row[0])
            self.assertIsNone(row[1])
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
