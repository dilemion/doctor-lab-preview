#!/usr/bin/env python3
"""Refresh live prices and optional private Sheets content, then run validation.

The content snapshot stays intact if Sheets are not configured. When requested,
Sheets errors abort publication; individual importers keep their last good files.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

def run(command, root):
    subprocess.run(command, cwd=root, check=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--description-budget", type=int, default=int(os.environ.get("DOCNLAB_DESCRIPTION_BUDGET", "100")))
    parser.add_argument("--sheet", default=os.environ.get("DOCNLAB_SHEET_ID"))
    parser.add_argument("--oauth", action="store_true", help="Use existing local user OAuth only")
    parser.add_argument("--require-content", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.skip_prices:
        run([sys.executable, "scripts/sync_analyses.py", "--output", "data/analyses.json", "--description-budget", str(args.description_budget)], root)
    if args.sheet:
        command = [sys.executable, "scripts/sync_content.py", "--sheet", args.sheet, "--output", "data/content.json", "--catalog", "data/analyses.json"]
        command += ["--oauth"] if args.oauth else ["--service-account-env", "GOOGLE_SERVICE_ACCOUNT_JSON"]
        run(command, root)
    elif args.require_content:
        raise ValueError("A private Google Sheets ID is required")
    else:
        print("Sheets are not configured: preserving the last published content snapshot")
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], root)
    print("Data refresh and validation completed")

if __name__ == "__main__":
    main()
