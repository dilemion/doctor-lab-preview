import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sync_local_content as sync

class LocalPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name) / "repository"
        self.repository.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repository / "README.md").write_text("fixture")
        self.git("add", "README.md")
        self.git("commit", "-m", "fixture")
        self.git("remote", "add", "origin", "https://github.com/dilemion/doctor-lab-preview.git")
    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repository), *args], check=True, capture_output=True, text=True)
    def test_requires_main_before_any_publication(self):
        self.git("checkout", "-b", "content-edit")
        with self.assertRaisesRegex(ValueError, "main branch"):
            sync.validate_preview(self.repository)
    def test_main_with_expected_remote_is_allowed(self):
        sync.validate_preview(self.repository)
    def test_unrelated_destination_is_rejected(self):
        self.git("remote", "set-url", "origin", "https://github.com/example/other.git")
        with self.assertRaisesRegex(ValueError, "limited"):
            sync.validate_preview(self.repository)
    def test_user_changes_are_preserved(self):
        (self.repository / "README.md").write_text("user edit")
        with self.assertRaisesRegex(ValueError, "uncommitted"):
            sync.validate_preview(self.repository)
        self.assertEqual("user edit", (self.repository / "README.md").read_text())

if __name__ == "__main__":
    unittest.main()
