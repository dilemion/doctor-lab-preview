#!/usr/bin/env python3
"""Update private Sheets locally using existing OAuth; publish public rows only.

No new Google access, key creation, or upload of OAuth credentials is performed.
The public repository is a separate clean checkout with its own Git credentials.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import os

from build_site import build

def command(args, directory):
    return subprocess.run(args, cwd=directory, check=True, capture_output=True, text=True)

def validate_preview(preview):
    branch = command(["git", "branch", "--show-current"], preview).stdout.strip()
    if branch != "main":
        raise ValueError("Automatic content publication requires the main branch")
    remote = command(["git", "remote", "get-url", "origin"], preview).stdout.strip()
    allowed = {"https://github.com/dilemion/doctor-lab-preview", "git@github.com:dilemion/doctor-lab-preview"}
    if remote.rstrip("/").removesuffix(".git") not in allowed:
        raise ValueError("Automatic publication is limited to the Docnlab preview repository")
    if command(["git", "status", "--porcelain"], preview).stdout.strip():
        raise ValueError("Preview checkout contains uncommitted work; automatic update stopped")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--preview-repository", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    preview = args.preview_repository.resolve()
    validate_preview(preview)
    command(["git", "pull", "--ff-only", "origin", "main"], preview)
    with tempfile.TemporaryDirectory(prefix="docnlab-content-") as temporary:
        snapshot = Path(temporary) / "content.json"
        result = command([sys.executable, str(root / "scripts/sync_content.py"),
                 "--sheet", args.sheet, "--oauth", "--catalog", str(preview / "data/analyses.json"), "--output", str(snapshot)], root)
        print("Private Sheets read and validation completed")
        content = json.loads(snapshot.read_text(encoding="utf-8"))
        for group in ("doctors", "reviews", "branches", "promotions"):
            if any(row.get("status") != "published" for row in content.get(group, [])):
                raise ValueError("Draft rows cannot be published")
        destination = preview / "data/content.json"
        fd, staged = tempfile.mkstemp(prefix="content-", suffix=".json", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(snapshot.read_text(encoding="utf-8"))
            os.replace(staged, destination)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
        exported = build(preview, Path(temporary) / "public")
        command(["git", "add", "data/content.json"], preview)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=preview).returncode
        if changed:
            command(["git", "commit", "-m", "Обновил опубликованный контент из Google Sheets"], preview)
            command(["git", "push", "origin", "main"], preview)
        command([sys.executable, str(root / "scripts/publish_pages.py"),
                 "--repository", str(preview), "--source", str(exported)], preview)
        print("Published content is available at https://dilemion.github.io/doctor-lab-preview/")

if __name__ == "__main__":
    main()
