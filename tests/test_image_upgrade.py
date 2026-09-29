import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sfera_monitor", ROOT / "sfera_monitor.py")
MONITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)


class ImageUpgradeTests(unittest.TestCase):
    def test_bijou_thumbnail_and_size_suffix(self):
        self.assertEqual(
            MONITOR.upgrade_image_url("https://www.bijou-brigitte.com/thumbnail/aa/bb/142749660_0_tb.webp"),
            "https://www.bijou-brigitte.com/media/aa/bb/142749660_0.webp",
        )
        self.assertEqual(
            MONITOR.upgrade_image_url("https://www.bijou-brigitte.com/media/aa/bb/142749660_0_400x400.webp"),
            "https://www.bijou-brigitte.com/media/aa/bb/142749660_0.webp",
        )

    def test_shopify_width_and_named_size(self):
        self.assertEqual(
            MONITOR.upgrade_image_url("https://cdn.shopify.com/s/files/1/img_100x.jpg?v=1&width=180"),
            "https://cdn.shopify.com/s/files/1/img.jpg?v=1",
        )
        self.assertEqual(
            MONITOR.upgrade_image_url("https://cdn.shopify.com/s/files/1/img_large.jpg"),
            "https://cdn.shopify.com/s/files/1/img.jpg",
        )

    def test_primark_amplience_presets(self):
        self.assertEqual(
            MONITOR.upgrade_image_url("https://media.primark.com/i/primark/991175075260_01?w=400"),
            "https://media.primark.com/i/primark/991175075260_01?$articleimages-largedesktop$&fmt=auto",
        )
        self.assertEqual(
            MONITOR.upgrade_image_url("https://media.primark.com/i/primark/991175075260?$productimages$"),
            "https://media.primark.com/i/primark/991175075260?$productimages-largedesktop$&fmt=auto",
        )

    def test_sfera_dam_width(self):
        self.assertEqual(
            MONITOR.upgrade_image_url(
                "https://dam.elcorteingles.es/producto/www-123-00.jpg?impolicy=Resize&width=967&height=1200"
            ),
            "https://dam.elcorteingles.es/producto/www-123-00.jpg?impolicy=Resize&width=1600",
        )

    def test_unique_site_urls_upgrades_and_dedupes(self):
        urls = MONITOR.unique_urls(
            [
                "/thumbnail/aa/bb/142749660_0_400x400.webp",
                "https://www.bijou-brigitte.com/media/aa/bb/142749660_0.webp",
            ]
        )
        self.assertEqual(urls, ["https://www.bijou-brigitte.com/media/aa/bb/142749660_0.webp"])

    def test_primark_collects_jsonld_image_list(self):
        html = '''
        <script type="application/ld+json">
        {"@type":"ItemList","itemListElement":[{"item":{
            "name":"Beaded bag",
            "sku":"991175075260",
            "url":"https://www.primark.com/en-us/p/beaded-bag-991175075260",
            "image":[
                "https://media.primark.com/i/primark/991175075260_01?w=200",
                {"url":"https://media.primark.com/i/primark/991175075260_02"}
            ],
            "offers":{"price":"8.00"}
        }}]}
        </script>
        '''
        products = MONITOR.primark_products_from_html(html)
        self.assertEqual(len(products), 1)
        mapped = MONITOR.map_primark_product(products[0])
        self.assertEqual(
            mapped["image_candidates"],
            [
                "https://media.primark.com/i/primark/991175075260_01?$articleimages-largedesktop$&fmt=auto",
                "https://media.primark.com/i/primark/991175075260_02?$articleimages-largedesktop$&fmt=auto",
            ],
        )

    def test_refine_keeps_upgraded_white_candidate(self):
        product = {
            "site": "lovisa",
            "image_url": "https://cdn.shopify.com/s/files/1/model_100x.jpg",
            "image_candidates": [
                "https://cdn.shopify.com/s/files/1/model_100x.jpg",
                "https://cdn.shopify.com/s/files/1/white_100x.jpg",
            ],
        }
        with patch.object(MONITOR, "has_white_background", side_effect=lambda url: "white" in url):
            MONITOR.refine_product_image(product)
        self.assertEqual(product["image_url"], "https://cdn.shopify.com/s/files/1/white.jpg")


if __name__ == "__main__":
    unittest.main()
