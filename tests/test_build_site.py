import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts/build_site.py"
spec = importlib.util.spec_from_file_location("build_site", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        for directory in module.PUBLIC_DIRS:
            (self.source / directory).mkdir(parents=True)
        (self.source / "index.html").write_text("<h1>Site</h1>")
        (self.source / "analyses/index.html").write_text("<h1>Analyses</h1>")
        (self.source / "data/analyses.json").write_text(json.dumps({"services": [{"code": "62.056", "price_rub": 5760}]}))
        (self.source / "data/content.json").write_text(json.dumps({"doctors": [], "reviews": [], "branches": [], "promotions": []}))

    def test_only_public_content_exported(self):
        (self.source / "private-note.md").write_text("private")
        (self.source / "data/token.json").write_text("secret")
        (self.source / "site/.env").write_text("secret")
        destination = module.build(self.source, self.base / "public")
        self.assertTrue((destination / "data/analyses.json").is_file())
        self.assertFalse((destination / "private-note.md").exists())
        self.assertFalse((destination / "data/token.json").exists())
        self.assertFalse((destination / "site/.env").exists())
        self.assertIn("../variants/01-lab/index.html", (destination / "01-lab/index.html").read_text())

    def test_local_photo_assets_exported(self):
        (self.source / "assets").mkdir()
        (self.source / "assets/doctor.jpg").write_bytes(b"image")
        destination = module.build(self.source, self.base / "public")
        self.assertEqual(b"image", (destination / "assets/doctor.jpg").read_bytes())

    def test_draft_rows_cannot_be_published(self):
        (self.source / "data/content.json").write_text(json.dumps({"doctors": [{"id": "demo", "status": "draft"}]}))
        with self.assertRaisesRegex(ValueError, "Non-published"):
            module.build(self.source, self.base / "public")

    def test_failed_build_keeps_existing_output(self):
        destination = self.base / "public"
        destination.mkdir()
        (destination / "keep.txt").write_text("unchanged")
        with self.assertRaisesRegex(ValueError, "empty"):
            module.build(self.source, destination)
        self.assertEqual("unchanged", (destination / "keep.txt").read_text())

    def test_cannot_build_into_source_tree(self):
        with self.assertRaisesRegex(ValueError, "overwrite"):
            module.build(self.source, self.source / "site" / "nested")

if __name__ == "__main__":
    unittest.main()
