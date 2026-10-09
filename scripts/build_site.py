#!/usr/bin/env python3
"""Export only public website files to a new output directory."""
import argparse
import json
from pathlib import Path
import shutil

PUBLIC_DIRS = ("variants", "site", "analyses", "data")
OPTIONAL_DIRS = ("assets",)
LEGACY_ROUTES = ("01-lab", "02-doctors", "03-hybrid", "04-private", "05-digital")

def build(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination == source or any(destination == source / part or source / part in destination.parents for part in PUBLIC_DIRS + OPTIONAL_DIRS):
        raise ValueError("Output must not overwrite a public source directory")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Output directory must be empty; existing files are preserved")
    for required in ("index.html", "analyses/index.html", "data/analyses.json", "data/content.json"):
        if (source / required).is_symlink():
            raise ValueError("Symlink is not allowed in public source file")
        if not (source / required).is_file():
            raise ValueError("Missing public file: " + required)
    prices = json.loads((source / "data/analyses.json").read_text(encoding="utf-8"))
    content = json.loads((source / "data/content.json").read_text(encoding="utf-8"))
    if not prices.get("services"):
        raise ValueError("Cannot export empty analysis prices")
    for group in ("doctors", "reviews", "branches", "promotions"):
        if any(row.get("status") != "published" for row in content.get(group, [])):
            raise ValueError("Non-published content in public snapshot: " + group)
    destination.mkdir(parents=True, exist_ok=True)
    for name in PUBLIC_DIRS + OPTIONAL_DIRS:
        path = source / name
        if name in OPTIONAL_DIRS and not path.exists():
            continue
        if path.is_symlink():
            raise ValueError("Symlink is not allowed in public source directory")
        if not path.is_dir():
            raise ValueError("Missing public directory: " + name)
        for child in path.rglob("*"):
            if child.is_symlink():
                raise ValueError("Symlink is not allowed in public export: " + str(child))
            if child.is_file():
                if name == "data" and child.name not in {"analyses.json", "content.json"}:
                    continue
                if name != "data" and child.suffix.lower() not in {".html", ".css", ".js", ".jpg", ".jpeg", ".png", ".webp", ".svg", ".gif", ".avif", ".woff", ".woff2", ".ico"}:
                    continue
                target = destination / child.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(child, target)
    shutil.copyfile(source / "index.html", destination / "index.html")
    (destination / ".nojekyll").write_text("", encoding="utf-8")
    for route in LEGACY_ROUTES:
        path = destination / route
        path.mkdir(exist_ok=True)
        html = ('<!doctype html><html lang="ru"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<meta http-equiv="refresh" content="0;url=../variants/' + route + '/index.html">'
                '<title>Доктор и Лаборатория</title><a href="../variants/' + route + '/index.html">Открыть сайт</a></html>')
        (path / "index.html").write_text(html, encoding="utf-8", newline="\n")
    print("Public website exported:", destination)
    return destination

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)
