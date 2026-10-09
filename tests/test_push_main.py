from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from push_main import push_main

class ConcurrentPublicationTests(unittest.TestCase):
    def test_price_and_content_updates_are_both_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            remote, prices, content = base / "remote.git", base / "prices", base / "content"
            def git(path, *args):
                return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "clone", str(remote), str(prices)], check=True, capture_output=True)
            git(prices, "config", "user.name", "Test"); git(prices, "config", "user.email", "test@example.invalid")
            (prices / "prices.json").write_text("old prices")
            (prices / "content.json").write_text("old content")
            git(prices, "add", "--all"); git(prices, "commit", "-m", "seed"); git(prices, "push", "origin", "main")
            subprocess.run(["git", "clone", str(remote), str(content)], check=True, capture_output=True)
            git(content, "config", "user.name", "Test"); git(content, "config", "user.email", "test@example.invalid")
            (prices / "prices.json").write_text("new prices")
            git(prices, "add", "prices.json"); git(prices, "commit", "-m", "prices")
            (content / "content.json").write_text("new content")
            git(content, "add", "content.json"); git(content, "commit", "-m", "content"); git(content, "push", "origin", "main")
            push_main(prices)
            self.assertEqual("new prices", git(remote, "show", "main:prices.json").stdout)
            self.assertEqual("new content", git(remote, "show", "main:content.json").stdout)
            self.assertEqual("new content", (prices / "content.json").read_text())

if __name__ == "__main__":
    unittest.main()
