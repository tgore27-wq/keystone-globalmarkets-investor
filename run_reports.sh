#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_reports.sh  —  GlobalMarkets-Investor report automation
#
# Usage:
#   ./run_reports.sh open          # run open report for today
#   ./run_reports.sh close         # run close report for today
#   ./run_reports.sh weekly        # run weekly recap (use on Fridays)
#   ./run_reports.sh all           # open + close + weekly (Friday)
#
# Called by cron — logs to run_reports.log
# ---------------------------------------------------------------------------
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/run_reports.log"
TYPE="${1:-open}"
DATE=$(date +%Y-%m-%d)
PYTHON="/usr/local/bin/python3"

echo "" >> "$LOG"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') | type=$TYPE ===" >> "$LOG"

cd "$DIR"

# Skip weekends
DAY=$(date +%u)   # 1=Mon … 7=Sun
if [[ "$DAY" -ge 6 ]]; then
  echo "Weekend — skipping." >> "$LOG"
  exit 0
fi

run_type() {
  local t="$1"
  echo "Running: generate_report.py --type $t --date $DATE" >> "$LOG"
  $PYTHON generate_report.py --type "$t" --date "$DATE" >> "$LOG" 2>&1
}

case "$TYPE" in
  open)   run_type open ;;
  close)  run_type close ;;
  weekly) run_type weekly ;;
  all)
    run_type open
    run_type close
    run_type weekly
    ;;
  *)
    echo "Unknown type: $TYPE. Use open|close|weekly|all" >> "$LOG"
    exit 1
    ;;
esac

# Stage and commit new/changed report files
echo "Committing..." >> "$LOG"
git add Open/ Close/ Weekly/ 2>> "$LOG" || true

if git diff --cached --quiet; then
  echo "Nothing new to commit." >> "$LOG"
else
  git commit -m "Auto: $TYPE report for $DATE" >> "$LOG" 2>&1
  git push origin main >> "$LOG" 2>&1
  echo "Pushed to GitHub." >> "$LOG"
fi

echo "Done." >> "$LOG"
