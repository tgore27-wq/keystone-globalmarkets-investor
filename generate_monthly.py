#!/usr/bin/env python3
"""
generate_monthly.py
Generates a monthly market report by aggregating price data and weekly summaries.

Usage:
  python generate_monthly.py                  # report for previous month
  python generate_monthly.py --month 2026-06  # specific month
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yfinance as yf

# ---------------------------------------------------------------------------
# Config (mirrors generate_report.py)
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
    for key in ("POLYGON_API_KEY", "ALPHA_VANTAGE_API_KEY", "FRED_API_KEY", "ANTHROPIC_API_KEY"):
        if key in os.environ:
            cfg[key] = os.environ[key]
    return cfg

ENV      = load_env()
POLY_KEY = ENV.get("POLYGON_API_KEY", "")
AV_KEY   = ENV.get("ALPHA_VANTAGE_API_KEY", "")
FRED_KEY = ENV.get("FRED_API_KEY", "")

POLY_BASE = "https://api.polygon.io"
FRED_BASE = "https://api.stlouisfed.org/fred"

# ---------------------------------------------------------------------------
# Ticker maps
# ---------------------------------------------------------------------------

INDICES = {
    "S&P 500":      "^GSPC",
    "Nasdaq":       "^IXIC",
    "Dow Jones":    "^DJI",
    "Russell 2000": "^RUT",
}

ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Healthcare",
    "XLE": "Energy",
    "XLY": "Consumer Discretionary",
    "XLI": "Industrials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}

CRYPTO_POLY = {
    "Bitcoin (BTC)":  "X:BTCUSD",
    "Ethereum (ETH)": "X:ETHUSD",
}

COMMODITIES_POLY = {
    "Gold (XAU)":   "C:XAUUSD",
    "Silver (XAG)": "C:XAGUSD",
}

COMMODITIES_YF = {
    "WTI Crude Oil": "CL=F",
    "Natural Gas":   "NG=F",
}

CREDIT_YF = {
    "HYG": "HYG",
    "LQD": "LQD",
}

FRED_SERIES = {
    "10Y Yield": "DGS10",
    "2Y Yield":  "DGS2",
}

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def month_bounds(year: int, month: int):
    """Return (first_trading_day, last_trading_day) of the month."""
    import calendar
    first = datetime(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    last = datetime(year, month, last_day)
    return first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")


def ytd_start(year: int) -> str:
    return f"{year}-01-01"

# ---------------------------------------------------------------------------
# Polygon helpers
# ---------------------------------------------------------------------------

_poly_calls = []

def _poly_wait():
    now = time.time()
    _poly_calls[:] = [t for t in _poly_calls if now - t < 60]
    if len(_poly_calls) >= 5:
        wait = 61 - (now - _poly_calls[0])
        if wait > 0:
            print(f"  [Polygon] rate limit — waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
    _poly_calls.append(time.time())


def poly_range(ticker, start, end):
    _poly_wait()
    url = (f"{POLY_BASE}/v2/aggs/ticker/{ticker}/range/1/day"
           f"/{start}/{end}?adjusted=true&sort=asc&limit=500&apiKey={POLY_KEY}")
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
            return []
        return data["results"]
    except Exception as e:
        print(f"  [Polygon error] {ticker}: {e}")
        return []


def poly_open_close(ticker, month_start, month_end):
    """Return (month_open, month_close) from Polygon daily bars."""
    bars = poly_range(ticker, month_start, month_end)
    if not bars:
        return None, None
    return round(bars[0]["o"], 2), round(bars[-1]["c"], 2)

# ---------------------------------------------------------------------------
# yfinance helpers
# ---------------------------------------------------------------------------

def yf_open_close(ticker, month_start, month_end, ytd_s):
    """Return (month_open, month_close, ytd_pct) using yfinance."""
    try:
        t = yf.Ticker(ticker)
        df = t.history(start=ytd_s, end=(datetime.strptime(month_end, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d"),
                       interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None, None, None
        df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index

        ytd_dt = datetime.strptime(ytd_s, "%Y-%m-%d")
        m_start_dt = datetime.strptime(month_start, "%Y-%m-%d")
        m_end_dt   = datetime.strptime(month_end, "%Y-%m-%d")

        ytd_rows  = df[df.index >= ytd_dt]
        mon_rows  = df[(df.index >= m_start_dt) & (df.index <= m_end_dt)]

        if mon_rows.empty:
            return None, None, None

        m_open  = round(float(mon_rows.iloc[0]["Open"]), 2)
        m_close = round(float(mon_rows.iloc[-1]["Close"]), 2)

        ytd_pct = None
        if not ytd_rows.empty:
            ytd_open  = float(ytd_rows.iloc[0]["Open"])
            ytd_pct   = round((m_close - ytd_open) / ytd_open * 100, 2)

        return m_open, m_close, ytd_pct
    except Exception as e:
        print(f"  [yfinance error] {ticker}: {e}")
        return None, None, None

# ---------------------------------------------------------------------------
# FRED helpers
# ---------------------------------------------------------------------------

def fred_on_date(series_id, date_str):
    try:
        url = (f"{FRED_BASE}/series/observations"
               f"?series_id={series_id}&sort_order=desc&limit=5"
               f"&observation_end={date_str}&api_key={FRED_KEY}&file_type=json")
        r = requests.get(url, timeout=10)
        for o in r.json().get("observations", []):
            if o["value"] not in (".", ""):
                return float(o["value"])
    except Exception as e:
        print(f"  [FRED error] {series_id}: {e}")
    return None

# ---------------------------------------------------------------------------
# Alpha Vantage — monthly top gainers/losers (best-effort)
# ---------------------------------------------------------------------------

def fetch_monthly_movers():
    if not AV_KEY:
        return [], []
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "TOP_GAINERS_LOSERS", "apikey": AV_KEY},
            timeout=15,
        )
        data = r.json()
        if "Information" in data or "Note" in data:
            return [], []
        def filt(lst):
            return [x for x in lst if float(x.get("price", 0)) >= 5][:5]
        return filt(data.get("top_gainers", [])), filt(data.get("top_losers", []))
    except Exception:
        return [], []

# ---------------------------------------------------------------------------
# Weekly report extraction
# ---------------------------------------------------------------------------

def find_weekly_reports(year: int, month: int) -> list[Path]:
    """Find Weekly/*.md files whose Monday falls in the given month."""
    weekly_dir = BASE / "Weekly"
    if not weekly_dir.exists():
        return []
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    m_start = datetime(year, month, 1)
    m_end   = datetime(year, month, last_day)

    reports = []
    for f in sorted(weekly_dir.glob("Weekly_*.md")):
        if "Template" in f.name:
            continue
        # filename like Weekly_06-22-26.md → monday date
        stem = f.stem  # "Weekly_06-22-26"
        parts = stem.split("_", 1)
        if len(parts) < 2:
            continue
        dp = parts[1].split("-")  # ["06","22","26"]
        if len(dp) != 3:
            continue
        try:
            mon_date = datetime(2000 + int(dp[2]), int(dp[0]), int(dp[1]))
        except ValueError:
            continue
        # include if the Monday is in this month OR the week overlaps this month
        week_end = mon_date + timedelta(days=4)
        if mon_date <= m_end and week_end >= m_start:
            reports.append(f)
    return reports


def extract_week_summary(path: Path) -> tuple[str, str]:
    """Return (dates_label, summary_text) from a weekly report file."""
    content = path.read_text()

    # Try to extract the dates label from filename
    stem = path.stem  # Weekly_06-22-26
    dp = stem.split("_", 1)[-1].split("-")  # ["06","22","26"]
    try:
        mon = datetime(2000 + int(dp[2]), int(dp[0]), int(dp[1]))
        fri = mon + timedelta(days=4)
        dates_label = f"{mon.strftime('%b %-d')} – {fri.strftime('%b %-d, %Y')}"
    except (ValueError, IndexError):
        dates_label = stem

    # Pull first few lines of Market Summary if present
    summary = ""
    in_summary = False
    lines_collected = 0
    for line in content.splitlines():
        if "## 1. Market Summary" in line or "## Market Summary" in line:
            in_summary = True
            continue
        if in_summary:
            if line.startswith("## ") or line.startswith("---"):
                break
            if line.strip() and not line.startswith("[Fill"):
                summary += line.strip() + " "
                lines_collected += 1
            if lines_collected >= 6:
                break

    # Fallback: pull TL;DR bullets
    if not summary.strip():
        in_tldr = False
        bullets = []
        for line in content.splitlines():
            if "**TL;DR**" in line:
                in_tldr = True
                continue
            if in_tldr:
                if line.startswith("##") or line.startswith("---"):
                    break
                stripped = line.strip().lstrip("- ").strip()
                if stripped and not stripped.startswith("[Fill"):
                    bullets.append(f"- {stripped}")
        summary = "\n".join(bullets[:4]) if bullets else "_Summary not available._"
    else:
        summary = summary.strip()

    return dates_label, summary

# ---------------------------------------------------------------------------
# pct helper
# ---------------------------------------------------------------------------

def pct(open_val, close_val):
    if open_val and close_val and open_val != 0:
        return f"{((close_val - open_val) / abs(open_val)) * 100:+.2f}%"
    return ""

def fmt(val, decimals=2):
    return f"{val:,.{decimals}f}" if val is not None else ""

# ---------------------------------------------------------------------------
# Template population
# ---------------------------------------------------------------------------

def populate_template(year: int, month: int, data: dict) -> str:
    template_path = BASE / "Monthly" / "Monthly_Report_Template.md"
    content = template_path.read_text()

    month_dt    = datetime(year, month, 1)
    month_label = month_dt.strftime("%B %Y")
    month_slug  = month_dt.strftime("%m-%Y")

    import calendar
    last_day     = calendar.monthrange(year, month)[1]
    period_start = month_dt.strftime("%B 1, %Y")
    period_end   = datetime(year, month, last_day).strftime("%B %-d, %Y")

    next_month_dt    = (month_dt.replace(day=28) + timedelta(days=4)).replace(day=1)
    next_month_label = next_month_dt.strftime("%B %Y")

    now_str = datetime.now().strftime("%-I:%M %p ET")

    replacements = {
        "{{MONTH_LABEL}}":      month_label,
        "{{GENERATED}}":        now_str,
        "{{PERIOD_START}}":     period_start,
        "{{PERIOD_END}}":       period_end,
        "{{NEXT_MONTH_LABEL}}": next_month_label,
        "{{MONTH_SLUG}}":       month_slug,
    }
    for k, v in replacements.items():
        content = content.replace(k, v)

    # ---- Indices table ----
    idx_lines = []
    for name, (m_open, m_close, ytd_pct) in data["indices"].items():
        mo = fmt(m_open)
        mc = fmt(m_close)
        mp = pct(m_open, m_close)
        yp = f"{ytd_pct:+.2f}%" if ytd_pct is not None else ""
        idx_lines.append(f"| {name} | {mo} | {mc} | {mp} | {yp} |")
    content = content.replace(
        "| S&P 500 | | | | |\n| Nasdaq | | | | |\n| Dow Jones | | | | |\n| Russell 2000 | | | | |",
        "\n".join(idx_lines)
    )

    # ---- ETF table ----
    etf_lines = []
    for ticker, (sector, m_open, m_close, ytd_pct) in data["etfs"].items():
        mo = fmt(m_open)
        mc = fmt(m_close)
        mp = pct(m_open, m_close)
        yp = f"{ytd_pct:+.2f}%" if ytd_pct is not None else ""
        etf_lines.append(f"| {sector} | {ticker} | {mo} | {mc} | {mp} | {yp} |")
    old_etf = (
        "| Technology | XLK | | | | |\n"
        "| Financials | XLF | | | | |\n"
        "| Healthcare | XLV | | | | |\n"
        "| Energy | XLE | | | | |\n"
        "| Consumer Discretionary | XLY | | | | |\n"
        "| Industrials | XLI | | | | |\n"
        "| Utilities | XLU | | | | |\n"
        "| Real Estate | XLRE | | | | |"
    )
    content = content.replace(old_etf, "\n".join(etf_lines))

    # ---- Crypto table ----
    crypto_lines = []
    for name, (m_open, m_close, ytd_pct) in data["crypto"].items():
        mo = fmt(m_open)
        mc = fmt(m_close)
        mp = pct(m_open, m_close)
        yp = f"{ytd_pct:+.2f}%" if ytd_pct is not None else ""
        crypto_lines.append(f"| {name} | {mo} | {mc} | {mp} | {yp} |")
    content = content.replace(
        "| Bitcoin (BTC) | | | | |\n| Ethereum (ETH) | | | | |",
        "\n".join(crypto_lines)
    )

    # ---- Commodities table ----
    comm_lines = []
    for name, (m_open, m_close, ytd_pct) in data["commodities"].items():
        mo = fmt(m_open)
        mc = fmt(m_close)
        mp = pct(m_open, m_close)
        yp = f"{ytd_pct:+.2f}%" if ytd_pct is not None else ""
        comm_lines.append(f"| {name} | {mo} | {mc} | {mp} | {yp} |")
    content = content.replace(
        "| Gold (XAU) | | | | |\n| Silver (XAG) | | | | |\n| WTI Crude Oil | | | | |\n| Natural Gas | | | | |",
        "\n".join(comm_lines)
    )

    # ---- Yield table ----
    yields = data.get("yields", {})
    y2_start  = fmt(yields.get("2Y_start"), 2)
    y2_end    = fmt(yields.get("2Y_end"), 2)
    y10_start = fmt(yields.get("10Y_start"), 2)
    y10_end   = fmt(yields.get("10Y_end"), 2)

    y2_chg  = ""
    y10_chg = ""
    if yields.get("2Y_start") and yields.get("2Y_end"):
        bps = round((yields["2Y_end"] - yields["2Y_start"]) * 100)
        y2_chg = f"{bps:+d} bps"
    if yields.get("10Y_start") and yields.get("10Y_end"):
        bps = round((yields["10Y_end"] - yields["10Y_start"]) * 100)
        y10_chg = f"{bps:+d} bps"

    spread_start = ""
    spread_end   = ""
    spread_chg   = ""
    if yields.get("2Y_start") and yields.get("10Y_start"):
        s = round((yields["10Y_start"] - yields["2Y_start"]) * 100)
        spread_start = f"{s:+d} bps"
    if yields.get("2Y_end") and yields.get("10Y_end"):
        s = round((yields["10Y_end"] - yields["2Y_end"]) * 100)
        spread_end = f"{s:+d} bps"
    if yields.get("2Y_start") and yields.get("10Y_start") and yields.get("2Y_end") and yields.get("10Y_end"):
        s1 = (yields["10Y_start"] - yields["2Y_start"]) * 100
        s2 = (yields["10Y_end"]   - yields["2Y_end"]) * 100
        spread_chg = f"{round(s2 - s1):+d} bps"

    hyg_s = fmt(yields.get("HYG_start"))
    hyg_e = fmt(yields.get("HYG_end"))
    hyg_c = pct(yields.get("HYG_start"), yields.get("HYG_end"))
    lqd_s = fmt(yields.get("LQD_start"))
    lqd_e = fmt(yields.get("LQD_end"))
    lqd_c = pct(yields.get("LQD_start"), yields.get("LQD_end"))

    content = content.replace(
        "| 2-Year Treasury Yield | | | bps | |",
        f"| 2-Year Treasury Yield | {y2_start}% | {y2_end}% | {y2_chg} | |"
    )
    content = content.replace(
        "| 10-Year Treasury Yield | | | bps | |",
        f"| 10-Year Treasury Yield | {y10_start}% | {y10_end}% | {y10_chg} | |"
    )
    content = content.replace(
        "| 2Y / 10Y Spread | | | bps | |",
        f"| 2Y / 10Y Spread | {spread_start} | {spread_end} | {spread_chg} | |"
    )
    content = content.replace(
        "| HYG (High Yield Corp Bond) | | | % | |",
        f"| HYG (High Yield Corp Bond) | {hyg_s} | {hyg_e} | {hyg_c} | |"
    )
    content = content.replace(
        "| LQD (Inv. Grade Corp Bond) | | | % | |",
        f"| LQD (Inv. Grade Corp Bond) | {lqd_s} | {lqd_e} | {lqd_c} | |"
    )

    # ---- Week-by-week breakdown ----
    weeks = data.get("weeks", [])
    for i in range(1, 6):
        placeholder_dates   = f"{{{{WEEK{i}_DATES}}}}"
        placeholder_summary = f"{{{{WEEK{i}_SUMMARY}}}}"
        if i <= len(weeks):
            dates_lbl, summary = weeks[i - 1]
            content = content.replace(placeholder_dates, dates_lbl)
            content = content.replace(placeholder_summary, summary)
        else:
            content = content.replace(placeholder_dates, "")
            content = content.replace(placeholder_summary, "")

    # Remove Week 5 block if not needed
    if len(weeks) < 5:
        content = content.replace(
            "{{WEEK5_BLOCK}}",
            ""
        )
    else:
        dates_lbl, summary = weeks[4]
        week5_block = f"### Week 5 — {dates_lbl}\n{summary}"
        content = content.replace("{{WEEK5_BLOCK}}", week5_block)

    # ---- Top movers (best-effort from AV) ----
    gainers, losers = data.get("gainers", []), data.get("losers", [])
    gainer_rows = ""
    for i, g in enumerate(gainers[:5], 1):
        pct_str = g.get("change_percentage", "")
        gainer_rows += f"| {i} | {g.get('ticker','')} | | {pct_str} | |\n"
    if gainer_rows:
        old = ("| 1 | | | | |\n| 2 | | | | |\n| 3 | | | | |\n"
               "| 4 | | | | |\n| 5 | | | | |\n\n---\n\n## Month's Top Losers")
        new = gainer_rows.rstrip() + "\n\n---\n\n## Month's Top Losers"
        content = content.replace(old, new)

    loser_rows = ""
    for i, l in enumerate(losers[:5], 1):
        pct_str = l.get("change_percentage", "")
        loser_rows += f"| {i} | {l.get('ticker','')} | | {pct_str} | |\n"
    if loser_rows:
        old = ("| 1 | | | | |\n| 2 | | | | |\n| 3 | | | | |\n"
               "| 4 | | | | |\n| 5 | | | | |\n\n---\n\n## Fed Activity")
        new = loser_rows.rstrip() + "\n\n---\n\n## Fed Activity"
        content = content.replace(old, new)

    return content

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate monthly market report")
    parser.add_argument("--month", default=None,
                        help="Month to report on (YYYY-MM). Defaults to previous month.")
    args = parser.parse_args()

    if args.month:
        year, month = int(args.month[:4]), int(args.month[5:7])
    else:
        today = datetime.now()
        first_of_month = today.replace(day=1)
        prev = first_of_month - timedelta(days=1)
        year, month = prev.year, prev.month

    import calendar
    month_start, month_end = month_bounds(year, month)
    ytd_s = ytd_start(year)
    month_label = datetime(year, month, 1).strftime("%B %Y")
    month_slug  = datetime(year, month, 1).strftime("%m-%Y")
    out_path = BASE / "Monthly" / f"Monthly_{month_slug}.md"

    print(f"\n=== generate_monthly.py | {month_label} ===\n")
    print(f"Period: {month_start} → {month_end}")

    data = {"indices": {}, "etfs": {}, "crypto": {}, "commodities": {}, "yields": {}}

    # ---- Indices ----
    print("\nFetching indices (yfinance)...")
    for name, ticker in INDICES.items():
        o, c, ytd = yf_open_close(ticker, month_start, month_end, ytd_s)
        data["indices"][name] = (o, c, ytd)
        print(f"  {name}: {o} → {c}  ({pct(o,c)})  YTD: {ytd}")

    # ---- ETFs ----
    print("\nFetching sector ETFs (yfinance)...")
    for ticker, sector in ETFS.items():
        o, c, ytd = yf_open_close(ticker, month_start, month_end, ytd_s)
        data["etfs"][ticker] = (sector, o, c, ytd)
        print(f"  {ticker}: {o} → {c}")

    # ---- Crypto (Polygon) ----
    print("\nFetching crypto (Polygon)...")
    for name, poly_ticker in CRYPTO_POLY.items():
        o, c = poly_open_close(poly_ticker, month_start, month_end)
        # YTD via yf
        yf_ticker = "BTC-USD" if "BTC" in name else "ETH-USD"
        _, _, ytd = yf_open_close(yf_ticker, month_start, month_end, ytd_s)
        data["crypto"][name] = (o, c, ytd)
        print(f"  {name}: {o} → {c}")

    # ---- Commodities ----
    print("\nFetching commodities...")
    for name, poly_ticker in COMMODITIES_POLY.items():
        o, c = poly_open_close(poly_ticker, month_start, month_end)
        _, _, ytd = yf_open_close("GLD" if "Gold" in name else "SLV",
                                  month_start, month_end, ytd_s)
        data["commodities"][name] = (o, c, ytd)
        print(f"  {name}: {o} → {c}")
    for name, yf_ticker in COMMODITIES_YF.items():
        o, c, ytd = yf_open_close(yf_ticker, month_start, month_end, ytd_s)
        data["commodities"][name] = (o, c, ytd)
        print(f"  {name}: {o} → {c}")

    # ---- Yields (FRED) ----
    print("\nFetching yields (FRED)...")
    data["yields"]["2Y_start"]  = fred_on_date("DGS2",   month_start)
    data["yields"]["2Y_end"]    = fred_on_date("DGS2",   month_end)
    data["yields"]["10Y_start"] = fred_on_date("DGS10",  month_start)
    data["yields"]["10Y_end"]   = fred_on_date("DGS10",  month_end)
    print(f"  2Y: {data['yields']['2Y_start']} → {data['yields']['2Y_end']}")
    print(f"  10Y: {data['yields']['10Y_start']} → {data['yields']['10Y_end']}")

    # ---- Credit (HYG / LQD) ----
    print("\nFetching credit ETFs (yfinance)...")
    for ticker in ["HYG", "LQD"]:
        o, c, _ = yf_open_close(ticker, month_start, month_end, ytd_s)
        data["yields"][f"{ticker}_start"] = o
        data["yields"][f"{ticker}_end"]   = c
        print(f"  {ticker}: {o} → {c}")

    # ---- Weekly summaries ----
    print("\nExtracting weekly report summaries...")
    week_files = find_weekly_reports(year, month)
    print(f"  Found {len(week_files)} weekly report(s) for {month_label}")
    weeks = []
    for f in week_files:
        dates_lbl, summary = extract_week_summary(f)
        weeks.append((dates_lbl, summary))
        print(f"  → {f.name}: {dates_lbl}")
    data["weeks"] = weeks

    # ---- Top movers ----
    print("\nFetching top movers (Alpha Vantage)...")
    gainers, losers = fetch_monthly_movers()
    data["gainers"] = gainers
    data["losers"]  = losers

    # ---- Populate template ----
    print("\nPopulating template...")
    report = populate_template(year, month, data)
    out_path.write_text(report)
    print(f"\n✓ Monthly report saved: {out_path}")
    print("  Run fill_narratives.py --monthly to complete narrative sections.")


if __name__ == "__main__":
    main()
