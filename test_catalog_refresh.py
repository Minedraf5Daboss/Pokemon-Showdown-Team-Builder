from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from catalog_paths import active_catalog_dir, catalog_dir
from stage_pool import activate, collect_reports, monthly_reports, monthly_urls, read_json, sha256, write_json


class CatalogRefreshTests(unittest.TestCase):
    def test_latest_report_url_rolls_the_window_across_a_year(self):
        months = monthly_urls([
            "https://www.smogon.com/stats/2027-01/chaos/gen9doublesubers-1630.json",
            "https://smogon.com/stats/2026-12/",
        ])
        self.assertEqual(months, {
            "2026-11": "https://www.smogon.com/stats/2026-11/",
            "2026-12": "https://smogon.com/stats/2026-12/",
            "2027-01": "https://www.smogon.com/stats/2027-01/",
        })
        for url in ("https://example.com/stats/2027-01/", "https://www.smogon.com/stats/2027-13/",
                    "https://www.smogon.com/stats/2027-01/gen9ou-1500.txt"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                monthly_urls([url])

    def test_collection_reuses_checked_months_and_downloads_the_new_cutoffs(self):
        months = monthly_urls(["https://www.smogon.com/stats/2027-01/"])
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "stage"
            destination.mkdir()
            candidate_sources, download_manifest = [], []
            for month in ("2026-11", "2026-12"):
                (root / month).mkdir()
                all_rating = []
                for url, relative in monthly_reports(month, months[month]):
                    source = root / relative
                    source.write_bytes(url.encode())
                    record = {"url": url, "sha256": sha256(source)}
                    if relative.endswith(("-0.json", "-0.txt")):
                        all_rating.append(record)
                    else:
                        candidate_sources.append({**record, "path": relative})
                download_manifest.append({"month": month, "sources": all_rating})
            write_json(root / "candidate_sources.json", candidate_sources)
            write_json(root / "download_manifest.json", download_manifest)

            def response(request, **kwargs):
                if request.full_url.endswith(".json"):
                    return BytesIO(b"{}")
                if "/moveset/" in request.full_url:
                    return BytesIO(b"Sample moveset text")
                return BytesIO(b"| 1 | Pikachu | 50.00000% | 1 | 50.000% | 1 | 50.000% |")

            with patch("fetch_candidate_sources.urllib.request.urlopen", side_effect=response) as download:
                with self.assertRaisesRegex(ValueError, "Missing cached reports"):
                    collect_reports(root, destination, months, cached_only=True)
                download.assert_not_called()
                records = collect_reports(root, destination, months, cached_only=False)

            urls = {call.args[0].full_url for call in download.call_args_list}
            self.assertEqual(urls, {url for url, _ in monthly_reports("2027-01", months["2027-01"])})
            self.assertEqual(len(records), 12)
            manifest = read_json(destination / "download_manifest.json")
            self.assertEqual([item["month"] for item in manifest], list(months))
            self.assertTrue(all(len(item["sources"]) == 3 for item in manifest))
            for record in records + [source for item in manifest for source in item["sources"]]:
                self.assertEqual(record["sha256"], sha256(destination / record["path"]))

    def test_activation_and_rollback_require_a_validated_snapshot(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "candidate_pool.json").write_text("original", encoding="utf-8")
            snapshot = "2027-01-abcdef12"
            stage = catalog_dir(root, snapshot)
            stage.mkdir(parents=True)
            pool = stage / "candidate_pool.json"
            pool.write_text("staged", encoding="utf-8")
            self.assertEqual(active_catalog_dir(root), root)
            with self.assertRaises(FileNotFoundError):
                activate(root, snapshot)
            write_json(stage / "stage.json", {"snapshot": snapshot, "candidate_pool_sha256": sha256(pool)})
            activate(root, snapshot)
            self.assertEqual(active_catalog_dir(root), stage)
            activate(root, "original")
            pool.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match the validated snapshot"):
                activate(root, snapshot)
            self.assertEqual(active_catalog_dir(root), root)
