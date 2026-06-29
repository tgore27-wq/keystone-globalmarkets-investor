#!/usr/bin/env python3
"""
fill_narratives.py
Reads a generated investor report (price tables already populated),
fetches today's top market news via Alpha Vantage, then calls the
Claude API to fill every blank narrative section.

Usage:
  python fill_narratives.py --today          # open + close for today
  python fill_narratives.py --weekly         # weekly for current week
  python fill_narratives.py --all            # open + close + weekly
  python fill_narratives.py Open/Open_06-30-26.md
  python fill_narratives.py Close/Close_06-30-26.md Weekly/Weekly_06-29-26.md
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

try:
    import anthropic
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic", "-q"])
    import anthropic

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
    # Allow env vars to override .env
    for key in ("ANTHROPIC_API_KEY", "ALPHA_VANTAGE_API_KEY"):
        if key in os.environ:
            cfg[key] = os.environ[key]
    return cfg

ENV = load_env()
ANTHROPIC_KEY = ENV.get("ANTHROPIC_API_KEY", "")
AV_KEY = ENV.get("ALPHA_VANTAGE_API_KEY", "")

# ---------------------------------------------------------------------------
# News fetching (Alpha Vantage — free tier, 25 calls/day)
# ---------------------------------------------------------------------------

AV_NEWS_TICKERS = "SPY,QQQ,NVDA,AAPL,MSFT,TSLA,AMD,JPM,XOM"

def fetch_news(limit: int = 20) -> str:
    """Return a text block of today's top market headlines."""
    if not AV_KEY:
        return "(No Alpha Vantage key — news unavailable.)"
    try:
        url = (
            "https://www.alphavantage.co/query"
            f"?function=NEWS_SENTIMENT"
            f"&tickers={AV_NEWS_TICKERS}"
            f"&sort=LATEST"
            f"&limit={limit}"
            f"&apikey={AV_KEY}"
        )
        r = requests.get(url, timeout=15)
        data = r.json()
        items = data.get("feed", [])
        if not items:
            return "(No news returned by Alpha Vantage.)"
        lines = []
        for item in items[:limit]:
            title = item.get("title", "")
            source = item.get("source", "")
            summary = item.get("summary", "")[:200]
            tickers = ", ".join(
                t["ticker"] for t in item.get("ticker_sentiment", [])[:4]
            )
            lines.append(f"• [{source}] {title}")
            if summary:
                lines.append(f"  {summary}")
            if tickers:
                lines.append(f"  Related: {tickers}")
        return "\n".join(lines)
    except Exception as e:
        return f"(News fetch error: {e})"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior market analyst and financial writer at an institutional
investment firm. Your job is to complete investor-grade daily and weekly
market reports by filling in every blank or placeholder section.

RULES:
1. Do NOT change any existing data — prices, percentages, table values,
   dates, or headers. Preserve them exactly as written.
2. Fill ONLY sections that contain placeholders: "[Fill in]", empty "- ",
   blank table cells (| |), or text like "Bullish / Cautiously Bullish /
   Neutral / Cautious / Bearish".
3. Base all analysis on the price data already in the report plus the
   market news context provided. Do not invent numbers.
4. Market Tone choices (pick one): Bullish | Cautiously Bullish | Neutral |
   Cautious | Bearish
5. TL;DR: 4 concise bullets summarising the session's most important events.
6. Market Summary: 2-4 sentences of professional narrative prose.
7. Opportunities and Risks: 3 specific, actionable bullets each.
8. Geopolitical & Macro: short phrases, not long paragraphs.
9. Key Levels: fill Support, Resistance, Key Level, Notes for each row.
10. Sentiment Gauges: use CNN Fear & Greed value if present; estimate
    "Greed/Fear/Extreme Fear/Extreme Greed" from VIX and price action if not.
11. Earnings Calendar: fill in the most relevant earnings for this week
    based on context; use "—" if unknown.
12. Fed Watch: derive from current rate (shown in report) and recent
    yield-curve data.
13. After-Hours / Pre-Market Movers: if Catalyst column is blank, infer
    from the biggest daily movers in the sector ETF table.
14. Weekly sector rotation narrative: explain which sectors led/lagged
    and why, based on weekly % columns.
15. Return the COMPLETE report with every section filled. Output ONLY
    the report markdown — no commentary before or after.
"""

# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

def fill_with_claude(report_path: Path, news_text: str, report_date: str) -> str:
    """Call Claude API and return fully filled report markdown."""
    if not ANTHROPIC_KEY:
        print(f"  [ERROR] ANTHROPIC_API_KEY not set — cannot fill narratives.")
        return report_path.read_text()

    content = report_path.read_text()
    fname = report_path.name

    user_message = f"""\
Report date: {report_date}
File: {fname}

=== MARKET NEWS CONTEXT ===
{news_text}

=== REPORT TO COMPLETE ===
{content}
"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    print(f"  Calling Claude API for {fname}...")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"  [ERROR] Claude API call failed: {e}")
        return content   # return original if API fails

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_trading_day(date: datetime) -> bool:
    if date.weekday() >= 5:  # Sat=5, Sun=6
        return False
    # Juneteenth 2026
    if (date.year == 2026 and date.month == 6 and date.day == 19):
        return False
    # Independence Day observed (Jul 3 when Jul 4 is Sat)
    if (date.year == 2026 and date.month == 7 and date.day == 3):
        return False
    return True

def week_monday(d: datetime) -> datetime:
    return d - timedelta(days=d.weekday())

def report_date_from_path(path: Path) -> str:
    """Extract YYYY-MM-DD from filename like Open_06-30-26.md"""
    stem = path.stem  # e.g. "Open_06-30-26"
    parts = stem.split("_", 1)
    if len(parts) < 2:
        return datetime.now().strftime("%Y-%m-%d")
    date_part = parts[1]  # "06-30-26"
    p = date_part.split("-")
    if len(p) == 3:
        return f"20{p[2]}-{p[0]}-{p[1]}"
    return datetime.now().strftime("%Y-%m-%d")

def fill_file(path: Path, news_text: str):
    """Fill narratives for a single report file."""
    if not path.exists():
        print(f"  [SKIP] {path} not found.")
        return
    report_date = report_date_from_path(path)
    print(f"  Filling {path.name} (date: {report_date})")
    filled = fill_with_claude(path, news_text, report_date)
    path.write_text(filled)
    print(f"  ✓ {path.name} saved.")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fill narrative sections via Claude API")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--today",   action="store_true", help="Open + Close for today")
    group.add_argument("--weekly",  action="store_true", help="Weekly for current week")
    group.add_argument("--monthly", action="store_true", help="Monthly report (defaults to previous month)")
    group.add_argument("--all",     action="store_true", help="Open + Close + Weekly")
    parser.add_argument("files",    nargs="*",           help="Specific .md files to fill")
    parser.add_argument("--date",   default=None,        help="Override date YYYY-MM-DD")
    parser.add_argument("--month",  default=None,        help="Override month YYYY-MM (for --monthly)")
    args = parser.parse_args()

    today = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    date_str = today.strftime("%m-%d-%y")   # 06-30-26
    mon = week_monday(today)
    mon_str = mon.strftime("%m-%d-%y")

    print(f"\n=== fill_narratives.py | {today.strftime('%Y-%m-%d')} ===\n")
    print("Fetching market news...")
    news_text = fetch_news(limit=25)
    print(f"  Got {news_text.count(chr(10))} lines of news context.\n")

    targets: list[Path] = []

    if args.files:
        targets = [BASE / f for f in args.files]
    elif args.today:
        targets = [
            BASE / "Open"  / f"Open_{date_str}.md",
            BASE / "Close" / f"Close_{date_str}.md",
        ]
    elif args.weekly:
        targets = [BASE / "Weekly" / f"Weekly_{mon_str}.md"]
    elif args.monthly:
        if args.month:
            yr, mo = int(args.month[:4]), int(args.month[5:7])
        else:
            first = today.replace(day=1)
            prev  = first - timedelta(days=1)
            yr, mo = prev.year, prev.month
        from datetime import datetime as _dt
        month_slug = _dt(yr, mo, 1).strftime("%m-%Y")
        targets = [BASE / "Monthly" / f"Monthly_{month_slug}.md"]
    elif args.all:
        targets = [
            BASE / "Open"   / f"Open_{date_str}.md",
            BASE / "Close"  / f"Close_{date_str}.md",
            BASE / "Weekly" / f"Weekly_{mon_str}.md",
        ]
    else:
        # Default: fill all files that have placeholder text
        for folder in ("Open", "Close", "Weekly", "Monthly"):
            for f in sorted((BASE / folder).glob("*.md")):
                if "Template" in f.name:
                    continue
                content = f.read_text()
                if "[Fill in]" in content or "- \n- \n- " in content:
                    targets.append(f)
        if not targets:
            print("No unfilled reports found. Pass --today, --weekly, --monthly, or file paths.")
            return

    for path in targets:
        fill_file(path, news_text)
        time.sleep(2)   # brief pause between API calls

    print("\n✓ All done.")

if __name__ == "__main__":
    main()
