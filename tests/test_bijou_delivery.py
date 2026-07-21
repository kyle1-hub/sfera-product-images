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


if __name__ == "__main__":
    unittest.main()
