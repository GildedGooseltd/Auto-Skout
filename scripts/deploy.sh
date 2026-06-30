#!/bin/bash
# Build Skout and deploy to Netlify (*.netlify.app — no custom domain)
set -e
cd "$(dirname "$0")/.."

echo "Building Skout…"
.venv/bin/python src/main.py

SITE="$(pwd)/site"
echo ""
echo "✓ Built: $SITE/index.html"
echo ""

if command -v netlify &>/dev/null && [ -f .netlify/state.json ]; then
  echo "Deploying to Netlify…"
  netlify deploy --prod --dir=site
  echo ""
  echo "Live URL: $(netlify status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('url','(run: netlify open)'))" 2>/dev/null || echo "run: netlify open")"
  exit 0
fi

echo "=== First-time Netlify setup ==="
echo ""
echo "Option A — drag & drop (fastest):"
echo "  1. Open https://app.netlify.com/drop"
echo "  2. Drag the 'site' folder onto the page"
echo "  3. Bookmark the *.netlify.app URL Netlify gives you"
echo ""
echo "Option B — CLI (repeat deploys):"
echo "  npm install -g netlify-cli"
echo "  netlify login"
echo "  netlify init          # link this folder to your Netlify site (once)"
echo "  ./scripts/deploy.sh   # build + deploy"
echo ""
echo "Full guide: docs/netlify-deploy.md"
