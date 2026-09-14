"""The data locator catalog must not override the model's image profile."""
from pathlib import Path
import unittest

from scopex.data_catalog import load_data_catalog, render_runtime_catalog_summary

ROOT = Path(__file__).resolve().parents[1]


class CatalogModelCapacityBoundaryTests(unittest.TestCase):
    def test_builtin_catalog_has_no_competing_image_capacity(self):
        catalog = load_data_catalog(ROOT / "config/data-catalog.json")
        for source in catalog["sources"].values():
            self.assertNotIn("max_claim_images", source.get("access", {}))
        self.assertNotIn("max_claim_images=", render_runtime_catalog_summary(catalog))


if __name__ == "__main__":
    unittest.main()
