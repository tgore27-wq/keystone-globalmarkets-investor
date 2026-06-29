#!/usr/bin/env python3
"""
post_discord.py
Posts a completed market report to the appropriate Discord channel as a file attachment.

Usage:
  python post_discord.py --type open   --date 2026-06-29
  python post_discord.py --type close  --date 2026-06-29
  python post_discord.py --type weekly --date 2026-06-29   # date = any day in that week
  python post_discord.py --type monthly --month 2026-06
  python post_discord.py --file Open/Open_06-29-26.md      # explicit file path
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import tempfile
import requests

from convert_to_pdf import convert as md_to_pdf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent


def load_env():
    cfg = {}
    for f in [BASE / ".env", Path.home() / ".env"]:
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip()
    for key in ("DISCORD_WEBHOOK_OPEN", "DISCORD_WEBHOOK_CLOSE",
                "DISCORD_WEBHOOK_WEEKLY", "DISCORD_WEBHOOK_MONTHLY"):
        if key in os.environ:
            cfg[key] = os.environ[key]
    return cfg


ENV = load_env()

WEBHOOKS = {
    "open":    ENV.get("DISCORD_WEBHOOK_OPEN", ""),
    "close":   ENV.get("DISCORD_WEBHOOK_CLOSE", ""),
    "weekly":  ENV.get("DISCORD_WEBHOOK_WEEKLY", ""),
    "monthly": ENV.get("DISCORD_WEBHOOK_MONTHLY", ""),
}

# ---------------------------------------------------------------------------
# Title formatting
# ---------------------------------------------------------------------------

def format_title(report_type: str, path: Path) -> str:
    stem = path.stem  # e.g. Open_06-29-26, Weekly_06-22-26, Monthly_06-2026

    if report_type == "open":
        # Open_06-29-26 → June 29, 2026 Open Report
        dp = stem.split("_", 1)[-1].split("-")  # ["06","29","26"]
        try:
            dt = datetime(2000 + int(dp[2]), int(dp[0]), int(dp[1]))
            return f"{dt.strftime('%B %-d, %Y')} Open Report"
        except (ValueError, IndexError):
            return f"{stem} Open Report"

    if report_type == "close":
        dp = stem.split("_", 1)[-1].split("-")
        try:
            dt = datetime(2000 + int(dp[2]), int(dp[0]), int(dp[1]))
            return f"{dt.strftime('%B %-d, %Y')} Close Report"
        except (ValueError, IndexError):
            return f"{stem} Close Report"

    if report_type == "weekly":
        # Weekly_06-22-26 → Week of June 22, 2026 Weekly Report
        dp = stem.split("_", 1)[-1].split("-")  # ["06","22","26"]
        try:
            mon = datetime(2000 + int(dp[2]), int(dp[0]), int(dp[1]))
            fri = mon + timedelta(days=4)
            return f"Week of {mon.strftime('%B %-d')}–{fri.strftime('%-d, %Y')} Weekly Report"
        except (ValueError, IndexError):
            return f"{stem} Weekly Report"

    if report_type == "monthly":
        # Monthly_06-2026 → June 2026 Monthly Report
        dp = stem.split("_", 1)[-1].split("-")  # ["06","2026"]
        try:
            dt = datetime(int(dp[1]), int(dp[0]), 1)
            return f"{dt.strftime('%B %Y')} Monthly Report"
        except (ValueError, IndexError):
            return f"{stem} Monthly Report"

    return f"{stem} Report"

# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

def post_report(report_type: str, path: Path) -> bool:
    webhook_url = WEBHOOKS.get(report_type, "")
    if not webhook_url:
        print(f"  [ERROR] No webhook URL set for type '{report_type}'.")
        print(f"          Set DISCORD_WEBHOOK_{report_type.upper()} in .env or GitHub Secrets.")
        return False

    if not path.exists():
        print(f"  [ERROR] Report file not found: {path}")
        return False

    title = format_title(report_type, path)
    pdf_name = path.stem + ".pdf"

    print(f"  Converting to PDF: {path.name} → {pdf_name}")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / pdf_name
            md_to_pdf(path, pdf_path)
            print(f"  Posting to Discord: {title}")
            print(f"  File: {pdf_name}  →  #{report_type}-report channel")
            with open(pdf_path, "rb") as fh:
                resp = requests.post(
                    webhook_url,
                    data={"content": f"**{title}**"},
                    files={"file": (pdf_name, fh, "application/pdf")},
                    timeout=60,
                )
        if resp.status_code in (200, 204):
            print(f"  ✓ Posted successfully (HTTP {resp.status_code})")
            return True
        else:
            print(f"  [ERROR] Discord returned HTTP {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"  [ERROR] Failed to post to Discord: {e}")
        return False

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Post report to Discord as file attachment")
    parser.add_argument("--type",  choices=["open", "close", "weekly", "monthly"],
                        help="Report type")
    parser.add_argument("--date",  default=None, help="Date YYYY-MM-DD (for open/close/weekly)")
    parser.add_argument("--month", default=None, help="Month YYYY-MM (for monthly)")
    parser.add_argument("--file",  default=None, help="Explicit .md file path")
    args = parser.parse_args()

    if args.file:
        path = BASE / args.file if not Path(args.file).is_absolute() else Path(args.file)
        # Infer type from path
        name = path.stem.lower()
        if name.startswith("open"):
            report_type = "open"
        elif name.startswith("close"):
            report_type = "close"
        elif name.startswith("weekly"):
            report_type = "weekly"
        elif name.startswith("monthly"):
            report_type = "monthly"
        else:
            print("[ERROR] Cannot infer report type from filename. Use --type.")
            sys.exit(1)
        success = post_report(report_type, path)
        sys.exit(0 if success else 1)

    if not args.type:
        parser.print_help()
        sys.exit(1)

    report_type = args.type
    today = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()

    if report_type == "monthly":
        if args.month:
            yr, mo = int(args.month[:4]), int(args.month[5:7])
        else:
            first = today.replace(day=1)
            prev  = first - timedelta(days=1)
            yr, mo = prev.year, prev.month
        month_slug = datetime(yr, mo, 1).strftime("%m-%Y")
        path = BASE / "Monthly" / f"Monthly_{month_slug}.md"

    elif report_type == "weekly":
        # Monday of the week containing `today`
        monday = today - timedelta(days=today.weekday())
        mon_str = monday.strftime("%m-%d-%y")
        path = BASE / "Weekly" / f"Weekly_{mon_str}.md"

    elif report_type == "open":
        date_str = today.strftime("%m-%d-%y")
        path = BASE / "Open" / f"Open_{date_str}.md"

    elif report_type == "close":
        date_str = today.strftime("%m-%d-%y")
        path = BASE / "Close" / f"Close_{date_str}.md"

    print(f"\n=== post_discord.py | {report_type} | {today.strftime('%Y-%m-%d')} ===\n")
    success = post_report(report_type, path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
