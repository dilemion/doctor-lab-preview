from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import publish_pages as publisher

class PublicTreeTests(unittest.TestCase):
    def test_retired_files_removed_and_domain_configuration_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout, source = Path(temporary) / "checkout", Path(temporary) / "source"
            checkout.mkdir(); source.mkdir()
            subprocess.run(["git", "init", "-b", "gh-pages", str(checkout)], check=True, capture_output=True)
            for name in ("index.html", "retired.html", "CNAME"):
                (checkout / name).write_text("fixture")
            subprocess.run(["git", "-C", str(checkout), "add", "--all"], check=True, capture_output=True)
            (source / "index.html").write_text("new public page")
            self.assertEqual(["retired.html"], publisher.retired_files(checkout, source))
            publisher.git(checkout, "rm", "--force", "--", *publisher.retired_files(checkout, source))
            self.assertFalse((checkout / "retired.html").exists())
            self.assertTrue((checkout / "CNAME").exists())
            self.assertTrue((checkout / "index.html").exists())

if __name__ == "__main__":
    unittest.main()
