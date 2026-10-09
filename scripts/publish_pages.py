#!/usr/bin/env python3
"""Publish a validated public export to this repository's existing gh-pages.

Uses Git credentials locally, GITHUB_TOKEN in CI, and requests a legacy Pages
build explicitly so commits by GITHUB_TOKEN also update the public preview.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

REPOSITORY = "dilemion/doctor-lab-preview"

def git(root, *args, capture=False):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=capture, text=True, encoding="utf-8")

def retired_files(checkout, source):
    checkout, source = Path(checkout).resolve(), Path(source).resolve()
    current = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
    tracked = git(checkout, "ls-files", "-z", capture=True).stdout.split("\0")
    retired = []
    for name in tracked:
        if not name or name == "CNAME" or name in current:
            continue
        if not (checkout / name).resolve().is_relative_to(checkout):
            raise ValueError("Tracked public path escaped the publication checkout")
        retired.append(name)
    return retired

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Validated public export")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root, source = args.repository.resolve(), args.source.resolve()
    remote = git(root, "remote", "get-url", "origin", capture=True).stdout.strip()
    if remote.rstrip("/").removesuffix(".git") not in ("https://github.com/" + REPOSITORY, "git@github.com:" + REPOSITORY):
        raise ValueError("Publication is limited to the Docnlab preview repository")
    if not (source / "data/analyses.json").is_file() or not (source / ".nojekyll").exists():
        raise ValueError("Use build_site.py before publication")
    git(root, "fetch", "origin", "gh-pages")
    with tempfile.TemporaryDirectory(prefix="docnlab-pages-") as temporary:
        checkout = Path(temporary) / "checkout"
        git(root, "worktree", "add", "--detach", str(checkout), "origin/gh-pages")
        try:
            obsolete = retired_files(checkout, source)
            for offset in range(0, len(obsolete), 100):
                git(checkout, "rm", "--force", "--", *obsolete[offset:offset + 100])
            for item in source.rglob("*"):
                if item.is_symlink():
                    raise ValueError("Symlink in public export")
                if item.is_file():
                    target = checkout / item.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, target)
            git(checkout, "add", "--all")
            changed = subprocess.run(["git", "-C", str(checkout), "diff", "--cached", "--quiet"]).returncode
            if changed:
                git(checkout, "-c", "user.name=Docnlab data updater", "-c", "user.email=actions@users.noreply.github.com",
                    "commit", "-m", "Обновил данные и каталог Docnlab")
                git(checkout, "push", "origin", "HEAD:gh-pages")
            else:
                print("Public preview already matches the validated export")
        finally:
            git(root, "worktree", "remove", str(checkout))
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        credential = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                                    check=True, capture_output=True, text=True)
        fields = dict(line.split("=", 1) for line in credential.stdout.splitlines() if "=" in line)
        token = fields["password"]
    request = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + "/pages/builds", data=b"{}",
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "Docnlab-data-updater"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    print("GitHub Pages build requested:", result.get("status", "accepted"))

if __name__ == "__main__":
    main()
