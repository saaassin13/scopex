import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('deploy_frontend',Path(__file__).resolve().parents[1]/'scripts/deploy_frontend.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class FrontendPublishTests(unittest.TestCase):
    def test_publish_preserves_old_assets_and_rollback(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);src=root/'build';dst=root/'live';backups=root/'backup'
            for d in (src/'assets',dst/'assets'):d.mkdir(parents=True)
            (src/'index.html').write_text('<script src="./assets/new.js"></script>')
            (src/'assets/new.js').write_text('new')
            (dst/'index.html').write_text('old index')
            (dst/'assets/old.js').write_text('old')
            receipt=module.publish(src,dst,backups,'abc123')
            self.assertEqual((dst/'index.html').read_text(),(src/'index.html').read_text())
            self.assertEqual((dst/'assets/old.js').read_text(),'old')
            self.assertEqual((Path(receipt['backup'])/'dist-before/index.html').read_text(),'old index')
    def test_incomplete_build_does_not_touch_live_page(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);src=root/'build';dst=root/'live'
            src.mkdir();dst.mkdir()
            (src/'index.html').write_text('<script src="./assets/missing.js"></script>')
            (dst/'index.html').write_text('old')
            with self.assertRaises(ValueError):module.publish(src,dst,root/'backup','abc')
            self.assertEqual((dst/'index.html').read_text(),'old')
