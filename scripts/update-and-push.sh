#!/usr/bin/env bash
# Local fallback for when GitHub Actions is unavailable.
# Runs the health checks, commits the results and pushes to origin/main.
set -euo pipefail

cd "$(dirname "$0")/.."

python3 scripts/update.py --workers 24 --timeout 10

if git diff --quiet && git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi

git add -A
git commit -m "chore: refresh IPTV links $(date -u +%FT%TZ)"
git pull --rebase --autostash origin main
git push origin main
