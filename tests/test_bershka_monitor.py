import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sfera_monitor", ROOT / "sfera_monitor.py")
MONITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)


TARGET_URL = "https://www.bershka.com/es/mujer/accesorios/bisuteria-n3776.html"
CONFIG = {
    "base_url": TARGET_URL,
    "country": "es",
    "store_id": "44009500",
    "catalog_id": "40259530",
    "language_id": -5,
    "currency_symbol": "€",
    "state_namespace": "bershka-es",
    "categories": [
        {
            "name": "Bisutería",
            "url": TARGET_URL,
            "category_id": "3776",
            "api_category_id": "1010193140",
        }
    ],
}


class BershkaEsTests(unittest.TestCase):
    def test_target_url_category_id(self):
        self.assertEqual(MONITOR.extract_bershka_category_id(TARGET_URL), "3776")

    def test_es_api_urls_do_not_fallback_to_gb(self):
        urls = MONITOR.bershka_api_urls(CONFIG, CONFIG["categories"][0])
        self.assertTrue(urls)
        joined = "\n".join(urls)
        self.assertIn("44009500", joined)
        self.assertIn("40259530", joined)
        self.assertIn("languageId=-5", joined)
        self.assertIn("country=es", joined)
        self.assertIn("1010193140", joined)
        self.assertNotIn("44009506", joined)
        self.assertNotIn("country=gb", joined)
        self.assertNotIn("/gb/", joined)

    def test_mapping_uses_es_urls_euro_and_namespace(self):
        item = {
            "id": 123,
            "name": "Collar prueba",
            "price": 799,
            "seoUrl": "/es/mujer/accesorios/bisuteria/collar-prueba-c123.html",
            "detail": {
                "xmedia": [
                    {
                        "xmediaItems": [
                            {
                                "medias": [
                                    {
                                        "extraInfo": {
                                            "deliveryUrl": "https://static.bershka.net/assets/public/test/123-r.jpg"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
        }
        product = MONITOR.map_bershka_product(item, "Bisutería", TARGET_URL, CONFIG)
        self.assertEqual(product["site"], "bershka-es")
        self.assertEqual(product["product_id"], "bershka-es:123")
        self.assertTrue(product["url"].startswith("https://www.bershka.com/es/"))
        self.assertEqual(product["price"], "7,99 €")
        self.assertIn("123-r.jpg", product["image_url"])

    def test_state_namespace_isolated_from_gb_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MONITOR.Store(temp_dir)
            gb = {"site": "bershka", "product_id": "bershka:123", "name": "Old GB"}
            es = {"site": "bershka-es", "product_id": "bershka-es:123", "name": "ES"}
            self.assertTrue(store.mark_seen(gb))
            self.assertTrue(store.mark_seen(es))
            self.assertFalse(store.mark_seen(es))
            rows = store.conn.execute("SELECT product_id, site FROM products ORDER BY product_id").fetchall()
            self.assertEqual(rows, [("bershka-es:123", "bershka-es"), ("bershka:123", "bershka")])
            store.conn.close()

    def test_refine_bershka_es_uses_white_background_candidate(self):
        product = {
            "site": "bershka-es",
            "image_url": "https://static.bershka.net/assets/public/test/model-r.jpg",
            "image_candidates": [
                "https://static.bershka.net/assets/public/test/model-r.jpg",
                "https://static.bershka.net/assets/public/test/plain-s.jpg",
            ],
        }
        with patch.object(MONITOR, "has_white_background", side_effect=lambda url: "plain" in url):
            MONITOR.refine_product_image(product)
        self.assertEqual(product["image_url"], "https://static.bershka.net/assets/public/test/plain-s.jpg")

    def test_refine_bershka_es_drops_non_white_candidates(self):
        product = {
            "site": "bershka-es",
            "image_url": "https://static.bershka.net/assets/public/test/model-r.jpg",
            "image_candidates": [
                "https://static.bershka.net/assets/public/test/model-r.jpg",
                "https://static.bershka.net/assets/public/test/lifestyle-p.jpg",
            ],
        }
        with patch.object(MONITOR, "has_white_background", return_value=False):
            MONITOR.refine_product_image(product)
        self.assertEqual(product["image_url"], "")

    def test_bershka_category_zip_name_starts_with_site_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.jpg"
            Image.new("RGB", (10, 10), "white").save(source)
            product = {
                "site": "bershka-es",
                "product_id": "bershka-es:123",
                "name": "Collar prueba",
                "category": "Bisutería",
                "image_path": str(source),
            }
            _, category_zips, _, _ = MONITOR.build_product_zip_bundle(
                [product], temp_dir, TARGET_URL, "Bershka ES", "Newly appeared"
            )
        self.assertEqual(category_zips[0].name, "Bershka_ES_Bisutería_1款_Newly appeared.zip")

    def test_bershka_empty_category_fails(self):
        with patch.object(MONITOR, "fetch_bershka_category_products", return_value=[]):
            with self.assertRaises(RuntimeError):
                MONITOR.scrape_bershka(CONFIG)

    def test_baseline_only_does_not_send_wecom(self):
        args = type("Args", (), {"baseline_only": True, "force_new": False, "send": False})()
        config = {"state_dir": tempfile.mkdtemp(), "wecom_webhook": "", "download_images": False, "send_empty_report": True}
        product = {
            "site": "bershka-es",
            "product_id": "bershka-es:123",
            "name": "Collar prueba",
            "price": "7,99 €",
            "url": TARGET_URL,
            "image_url": "https://static.bershka.net/assets/public/test/123.jpg",
            "image_candidates": ["https://static.bershka.net/assets/public/test/123.jpg"],
            "category": "Bisutería",
        }
        store = MONITOR.Store(config["state_dir"])
        with patch.object(MONITOR, "scrape_site", return_value=[product]), \
             patch.object(MONITOR, "send_wecom") as send_wecom, \
             patch.object(MONITOR, "send_wecom_zip_bundle") as send_zip:
            MONITOR.process_site("bershka", config, store, args)
            send_wecom.assert_not_called()
            send_zip.assert_not_called()
        store.conn.close()


if __name__ == "__main__":
    unittest.main()
