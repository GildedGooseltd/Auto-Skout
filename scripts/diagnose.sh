#!/bin/bash
set -e
cd "$(dirname "$0")/.."
echo "=== Skout diagnose ==="
echo ""
echo "1. Network — can we reach Craigslist?"
curl -sL -o /dev/null -w "   HTTP %{http_code} in %{time_total}s\n" --max-time 15 \
  -A "Mozilla/5.0" "https://pueblo.craigslist.org/search/zip?sort=date" || echo "   FAILED"
echo ""
echo "2. Python — fetch free listings from Pueblo:"
cd src && ../.venv/bin/python -u -c "
from scrapers.craigslist import fetch_free
items = fetch_free('pueblo', 'zip', '')
print(f'   Found {len(items)} listings')
for i in items[:5]:
    print(f'   - {i.title[:60]}')
"
echo ""
echo "3. If step 2 shows listings, run:"
echo "   cd ~/free-stuff-alerts && .venv/bin/python src/main.py --test --open"
