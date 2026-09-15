from pathlib import Path
import unittest

from scopex.data_catalog import load_data_catalog, render_runtime_catalog_summary


class DataCatalogTimezoneTests(unittest.TestCase):
    def test_all_business_timestamp_sources_declare_timezone(self):
        catalog = load_data_catalog(Path(__file__).resolve().parents[1] / "config" / "data-catalog.json")
        for name, source in catalog["sources"].items():
            with self.subTest(source=name):
                self.assertEqual(source["layout"]["timezone"], "Asia/Shanghai")
        summary = render_runtime_catalog_summary(catalog)
        self.assertEqual(summary.count("timestamp timezone: Asia/Shanghai"), len(catalog["sources"]))
