#!/usr/bin/env python3
"""Preserve concurrent remote changes before publishing a main-branch commit."""
from pathlib import Path
import subprocess

def push_main(repository):
    repository = Path(repository).resolve()
    branch = subprocess.run(["git", "-C", str(repository), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    if branch != "main":
        raise ValueError("Publication requires the main branch")
    subprocess.run(["git", "-C", str(repository), "pull", "--rebase", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(repository), "push", "origin", "HEAD:main"], check=True)

if __name__ == "__main__":
    push_main(Path.cwd())
