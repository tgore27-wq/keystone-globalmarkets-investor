#!/usr/bin/env bash
# sync_local.sh  —  PRIVATE, LOCAL USE ONLY
# 1. Pulls the latest public report from GitHub
# 2. Appends Lucren content ideas to the local .md copy
# 3. Converts the complete local report (data + ideas) to PDF
#
# The PDF saved here includes content ideas and is private.
# The PDF posted to Discord (via GitHub Actions) is the public version only.
# ---------------------------------------------------------------------------
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$HOME/Library/Logs/GlobalMarkets-investor-sync.log"
PYTHON="/usr/local/bin/python3"
TYPE="${1:-open}"
DATE=$(date +%Y-%m-%d)
MM_DD_YY=$(date +%m-%d-%y)
MON_MM_DD_YY=$(date -v-Mon +%m-%d-%y 2>/dev/null || python3 -c "
from datetime import datetime, timedelta
d = datetime.now()
print((d - timedelta(days=d.weekday())).strftime('%m-%d-%y'))")

echo ""                                                           >> "$LOG"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') | sync type=$TYPE ===" >> "$LOG"

cd "$DIR"

# ── 1. Pull from GitHub ───────────────────────────────────────────────────
echo "Pulling from GitHub..." >> "$LOG"
/usr/bin/git pull origin main >> "$LOG" 2>&1 || {
  echo "[ERROR] git pull failed" >> "$LOG"
  exit 1
}

# ── 2. Append Lucren content ideas ───────────────────────────────────────
echo "Appending content ideas (type=$TYPE)..." >> "$LOG"
case "$TYPE" in
  open)
    MD_FILE="Open/Open_${MM_DD_YY}.md"
    $PYTHON append_content_ideas.py --open  --date "$DATE" >> "$LOG" 2>&1 || \
      echo "  [WARN] content ideas failed — report saved without ideas" >> "$LOG"
    ;;
  close)
    MD_FILE="Close/Close_${MM_DD_YY}.md"
    $PYTHON append_content_ideas.py --close --date "$DATE" >> "$LOG" 2>&1 || \
      echo "  [WARN] content ideas failed — report saved without ideas" >> "$LOG"
    ;;
  weekly)
    MD_FILE="Weekly/Weekly_${MON_MM_DD_YY}.md"
    $PYTHON append_content_ideas.py --weekly --date "$DATE" >> "$LOG" 2>&1 || \
      echo "  [WARN] content ideas failed — report saved without ideas" >> "$LOG"
    ;;
  monthly)
    PREV_MONTH=$(python3 -c "
from datetime import datetime, timedelta
d = datetime.now().replace(day=1) - timedelta(days=1)
print(d.strftime('%m-%Y'))")
    MD_FILE="Monthly/Monthly_${PREV_MONTH}.md"
    $PYTHON append_content_ideas.py --monthly >> "$LOG" 2>&1 || \
      echo "  [WARN] content ideas failed — report saved without ideas" >> "$LOG"
    ;;
  *)
    echo "[ERROR] Unknown type: $TYPE. Use open|close|weekly|monthly" >> "$LOG"
    exit 1
    ;;
esac

# ── 3. Convert to PDF (private copy — includes content ideas) ────────────
if [[ -f "$MD_FILE" ]]; then
  echo "Converting $MD_FILE to PDF..." >> "$LOG"
  $PYTHON convert_to_pdf.py "$MD_FILE" >> "$LOG" 2>&1 && \
    echo "  ✓ PDF saved: ${MD_FILE%.md}.pdf" >> "$LOG" || \
    echo "  [WARN] PDF conversion failed — .md file still available" >> "$LOG"
else
  echo "  [WARN] $MD_FILE not found — skipping PDF conversion" >> "$LOG"
fi

echo "Done." >> "$LOG"
