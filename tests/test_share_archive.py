import importlib.util
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sfera_monitor", ROOT / "sfera_monitor.py")
MONITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)


class ShareArchiveTests(unittest.TestCase):
    def test_share_run_dir_matches_existing_sfera_layout(self):
        name = MONITOR.share_run_dir_name("Sfera", 77, datetime(2026, 9, 20))
        self.assertEqual(name, "Sfera_网站上新_77款_20260920")

    def test_share_category_from_zip_name(self):
        self.assertEqual(
            MONITOR.share_category_from_zip_name("Sfera_PENDIENTES_16款_NUEVO.zip", "Sfera"),
            "PENDIENTES",
        )
        self.assertEqual(
            MONITOR.share_category_from_zip_name("Sfera_COLLARES Y CHOKERS_3款_NUEVO.zip", "Sfera"),
            "COLLARES Y CHOKERS",
        )
        self.assertEqual(
            MONITOR.share_category_from_zip_name("Lovisa_待补图片_4款_第1包.zip", "Lovisa"),
            "待补图片",
        )

    def test_copy_zips_into_site_category_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = Path(temp_dir) / "library"
            source = Path(temp_dir) / "Sfera_ANILLOS_1款_NUEVO.zip"
            source.write_bytes(b"zip")
            config = {
                "share_library_root": str(library),
                "share_site_folders": {"sfera": "E03-SFERA"},
            }
            result = MONITOR.copy_zips_to_share([source], config, "sfera", "Sfera", 1, day=datetime(2026, 9, 29))
            target = library / "E03-SFERA" / "ANILLOS" / "Sfera_ANILLOS_1款_NUEVO_20260929.zip"
            self.assertTrue(target.exists())
            self.assertEqual(result["copied"], [str(target)])
            again = Path(temp_dir) / "Sfera_ANILLOS_2款_NUEVO.zip"
            again.write_bytes(b"zip2")
            MONITOR.copy_zips_to_share([again], config, "sfera", "Sfera", 2, day=datetime(2026, 9, 30))
            self.assertTrue((library / "E03-SFERA" / "ANILLOS" / "Sfera_ANILLOS_2款_NUEVO_20260930.zip").exists())
            self.assertEqual(len(list((library / "E03-SFERA").iterdir())), 1)

    def test_copy_skips_unmapped_site(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "pack.zip"
            source.write_bytes(b"zip")
            result = MONITOR.copy_zips_to_share(
                [source],
                {"share_library_root": temp_dir, "share_site_folders": {"sfera": "E03-SFERA"}},
                "bijou",
                "Bijou Brigitte",
                1,
            )
        self.assertEqual(result["skipped"], "no-share-folder")

    def test_copy_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "pack.zip"
            source.write_bytes(b"zip")
            with patch.object(MONITOR.shutil, "copy2", side_effect=OSError("share offline")):
                result = MONITOR.copy_zips_to_share(
                    [source],
                    {
                        "share_library_root": temp_dir,
                        "share_site_folders": {"sfera": "E03-SFERA"},
                    },
                    "sfera",
                    "Sfera",
                    1,
                    day=datetime(2026, 9, 29),
                )
        self.assertEqual(result["error"], "share offline")
        self.assertEqual(result["copied"], [])

    def test_send_bundle_copies_category_zips_before_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from PIL import Image

            source = Path(temp_dir) / "source.jpg"
            Image.new("RGB", (10, 10), "white").save(source)
            product = {
                "site": "sfera",
                "product_id": "sfera:1",
                "name": "ANILLO",
                "category": "ANILLOS",
                "image_path": str(source),
            }
            library = Path(temp_dir) / "library"
            config = {
                "share_library_root": str(library),
                "share_site_folders": {"sfera": "E03-SFERA"},
            }
            with patch.object(MONITOR, "send_wecom", return_value={"errcode": 0}), patch.object(
                MONITOR, "send_wecom_file", return_value={"errcode": 0}
            ):
                result = MONITOR.send_wecom_zip_bundle(
                    "hook",
                    [product],
                    temp_dir,
                    "https://www.sfera.com/",
                    "Sfera",
                    "NUEVO",
                    config=config,
                    site_key="sfera",
                )
            copied = Path(result["share"]["copied"][0])
            self.assertEqual(copied.parent.parent.name, "E03-SFERA")
            self.assertEqual(copied.parent.name, "ANILLOS")
            self.assertTrue(copied.name.startswith("Sfera_ANILLOS_1款_NUEVO_"))
            self.assertTrue(copied.name.endswith(".zip"))
            self.assertTrue(copied.exists())
            self.assertEqual(result["cleanup"], "deleted")


if __name__ == "__main__":
    unittest.main()
