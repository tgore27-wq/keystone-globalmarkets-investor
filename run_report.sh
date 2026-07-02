#!/usr/bin/env bash
# run_report.sh — Full local pipeline: generate → fill → push → Discord → private PDF
# Called by crontab; replaces sync_local.sh + GitHub Actions schedule.
#
# Usage:
#   run_report.sh open
#   run_report.sh close
#   run_report.sh weekly
#   run_report.sh monthly
# ---------------------------------------------------------------------------
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$HOME/Library/Logs/GlobalMarkets-investor-sync.log"
PYTHON="/usr/local/bin/python3"
GIT="/usr/bin/git"
TYPE="${1:-open}"
DATE=$(date +%Y-%m-%d)
DATE_SLUG=$(date +%m-%d-%y)

# Monday of current week (for weekly filename)
MON_SLUG=$(date -v-Mon +%m-%d-%y 2>/dev/null || $PYTHON -c "
from datetime import datetime, timedelta
d = datetime.now()
print((d - timedelta(days=d.weekday())).strftime('%m-%d-%y'))")

echo ""                                                                   >> "$LOG"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') | run_report type=$TYPE ==="    >> "$LOG"

cd "$DIR"

# ── Load .env into environment ───────────────────────────────────────────────
if [[ -f "$DIR/.env" ]]; then
    while IFS='=' read -r key val; do
        [[ -z "$key" || "$key" == \#* ]] && continue
        export "$key"="$val"
    done < "$DIR/.env"
fi

# Determine target markdown file path
case "$TYPE" in
  open)
    MD="Open/Open_${DATE_SLUG}.md"
    ;;
  close)
    MD="Close/Close_${DATE_SLUG}.md"
    ;;
  weekly)
    MD="Weekly/Weekly_${MON_SLUG}.md"
    ;;
  monthly)
    PREV_MONTH=$($PYTHON -c "
from datetime import datetime, timedelta
d = datetime.now().replace(day=1) - timedelta(days=1)
print(d.strftime('%m-%Y'))")
    MD="Monthly/Monthly_${PREV_MONTH}.md"
    ;;
  *)
    echo "[ERROR] Unknown type: $TYPE — use open|close|weekly|monthly" >> "$LOG"
    exit 1
    ;;
esac

# ── 1. Generate price data ───────────────────────────────────────────────────
echo "1. Generating price data ($TYPE)..." >> "$LOG"
if [[ "$TYPE" == "monthly" ]]; then
    PREV_MONTH_YEAR=$($PYTHON -c "
from datetime import datetime, timedelta
d = datetime.now().replace(day=1) - timedelta(days=1)
print(d.strftime('%Y-%m'))")
    $PYTHON generate_monthly.py --month "$PREV_MONTH_YEAR" >> "$LOG" 2>&1
else
    $PYTHON generate_report.py --type "$TYPE" --date "$DATE" >> "$LOG" 2>&1
fi
echo "   ✓ Price data written to $MD" >> "$LOG"

# ── 2. Fill narratives with Claude API ──────────────────────────────────────
echo "2. Filling narratives (Claude API)..." >> "$LOG"
if [[ "$TYPE" == "monthly" ]]; then
    $PYTHON fill_narratives.py --monthly >> "$LOG" 2>&1
elif [[ "$TYPE" == "weekly" ]]; then
    $PYTHON fill_narratives.py --weekly --date "$DATE" >> "$LOG" 2>&1
else
    $PYTHON fill_narratives.py "$MD" --date "$DATE" >> "$LOG" 2>&1
fi
echo "   ✓ Narratives filled" >> "$LOG"

# ── 3. Commit and push to GitHub ────────────────────────────────────────────
echo "3. Committing and pushing..." >> "$LOG"
$GIT config user.name  "GlobalMarkets Bot"
$GIT config user.email "bot@globalmarkets.lucren"
$GIT add Open/ Close/ Weekly/ Monthly/ >> "$LOG" 2>&1
if $GIT diff --cached --quiet; then
    echo "   Nothing new to commit." >> "$LOG"
else
    $GIT commit -m "Auto: $TYPE report $DATE [price + narrative]" >> "$LOG" 2>&1
    $GIT push origin main >> "$LOG" 2>&1
    echo "   ✓ Pushed to GitHub" >> "$LOG"
fi

# ── 4. Post public PDF to Discord ───────────────────────────────────────────
echo "4. Posting PDF to Discord (#${TYPE}-report)..." >> "$LOG"
if [[ "$TYPE" == "monthly" ]]; then
    $PYTHON post_discord.py --type monthly >> "$LOG" 2>&1
else
    $PYTHON post_discord.py --type "$TYPE" --date "$DATE" >> "$LOG" 2>&1
fi
echo "   ✓ Posted to Discord" >> "$LOG"

# ── 5. Append private Lucren content ideas ──────────────────────────────────
echo "5. Appending Lucren content ideas (private)..." >> "$LOG"
if [[ "$TYPE" == "monthly" ]]; then
    $PYTHON append_content_ideas.py --monthly >> "$LOG" 2>&1 || \
        echo "   [WARN] Content ideas failed — report saved without ideas" >> "$LOG"
else
    $PYTHON append_content_ideas.py --"$TYPE" --date "$DATE" >> "$LOG" 2>&1 || \
        echo "   [WARN] Content ideas failed — report saved without ideas" >> "$LOG"
fi

# ── 6. Convert to private PDF (includes content ideas) ──────────────────────
if [[ -f "$MD" ]]; then
    echo "6. Converting to private PDF..." >> "$LOG"
    $PYTHON convert_to_pdf.py "$MD" >> "$LOG" 2>&1 && \
        echo "   ✓ Private PDF: ${MD%.md}.pdf" >> "$LOG" || \
        echo "   [WARN] PDF conversion failed" >> "$LOG"
fi

echo "=== Done: $TYPE $DATE $(date '+%H:%M:%S') ===" >> "$LOG"
