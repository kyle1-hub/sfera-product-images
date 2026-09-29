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
                "unknown-site",
                "Unknown",
                1,
            )
        self.assertEqual(result["skipped"], "no-share-folder")

    def test_copy_skips_unc_on_non_windows(self):
        unc = r"\\192.168.10.254\alpha_share\library"
        self.assertTrue(MONITOR.is_windows_unc_path(unc))
        with patch.object(MONITOR, "can_write_share_library", return_value=False):
            with tempfile.TemporaryDirectory() as temp_dir:
                source = Path(temp_dir) / "Sfera_ANILLOS_1款_NUEVO.zip"
                source.write_bytes(b"zip")
                inbox = Path(temp_dir) / "inbox"
                config = {
                    "share_library_root": unc,
                    "share_site_folders": {"sfera": "E03-SFERA"},
                    "share_inbox_dir": str(inbox),
                }
                result = MONITOR.archive_sent_zips(
                    [source],
                    config,
                    "sfera",
                    "Sfera",
                    1,
                    day=datetime(2026, 9, 29),
                )
                stashed = inbox / "E03-SFERA" / "ANILLOS" / "Sfera_ANILLOS_1款_NUEVO_20260929.zip"
                self.assertTrue(stashed.exists())
                self.assertEqual(result["copied"], [])
                self.assertEqual(result["error"], "unc-not-reachable")
                self.assertEqual(result["inbox"], [str(stashed)])

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

    def test_archive_stashes_when_share_copy_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Sfera_ANILLOS_1款_NUEVO.zip"
            source.write_bytes(b"zip")
            inbox = Path(temp_dir) / "inbox"
            config = {
                "share_library_root": str(Path(temp_dir) / "library"),
                "share_site_folders": {"sfera": "E03-SFERA"},
                "share_inbox_dir": str(inbox),
            }
            original_copy = MONITOR.shutil.copy2

            def copy_side_effect(src, dst):
                if str(inbox) in str(dst):
                    Path(dst).parent.mkdir(parents=True, exist_ok=True)
                    return original_copy(src, dst)
                raise OSError("share offline")

            with patch.object(MONITOR.shutil, "copy2", side_effect=copy_side_effect):
                result = MONITOR.archive_sent_zips(
                    [source],
                    config,
                    "sfera",
                    "Sfera",
                    1,
                    day=datetime(2026, 9, 29),
                )
            stashed = inbox / "E03-SFERA" / "ANILLOS" / "Sfera_ANILLOS_1款_NUEVO_20260929.zip"
            self.assertTrue(stashed.exists())
            self.assertEqual(result["copied"], [])
            self.assertEqual(result["inbox"], [str(stashed)])

    def test_sync_share_inbox_copies_then_deletes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = Path(temp_dir) / "inbox"
            library = Path(temp_dir) / "library"
            zipped = inbox / "E03-SFERA" / "ANILLOS" / "Sfera_ANILLOS_1款_NUEVO_20260929.zip"
            zipped.parent.mkdir(parents=True, exist_ok=True)
            zipped.write_bytes(b"zip")
            result = MONITOR.sync_share_inbox(
                {
                    "share_library_root": str(library),
                    "share_inbox_dir": str(inbox),
                }
            )
            dest = library / "E03-SFERA" / "ANILLOS" / "Sfera_ANILLOS_1款_NUEVO_20260929.zip"
            self.assertTrue(dest.exists())
            self.assertFalse(zipped.exists())
            self.assertEqual(result["copied"], [str(dest)])
            self.assertFalse((inbox / "E03-SFERA").exists())

    def test_send_bundle_stashes_when_share_offline(self):
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
            inbox = Path(temp_dir) / "inbox"
            config = {
                "share_library_root": r"\\offline-share\library",
                "share_site_folders": {"sfera": "E03-SFERA"},
                "share_inbox_dir": str(inbox),
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
            self.assertTrue(result["share"]["inbox"])
            self.assertTrue(Path(result["share"]["inbox"][0]).exists())
            self.assertTrue(Path(result["share"]["inbox"][0]).name.startswith("Sfera_ANILLOS_1款_NUEVO_"))


if __name__ == "__main__":
    unittest.main()
