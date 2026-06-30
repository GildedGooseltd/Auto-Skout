# Host Skout on Netlify (no custom domain)

Skout is a static dashboard (`site/index.html` + `site/data.json`). Netlify hosts it for free. You do **not** need gildedgoosegarage.com or Squarespace DNS.

---

## Step 1 — Build

```bash
cd ~/free-stuff-alerts
.venv/bin/python src/main.py
```

This writes fresh files to `site/`.

---

## Step 2 — First deploy (drag & drop, ~2 min)

1. Go to [app.netlify.com/drop](https://app.netlify.com/drop)
2. Sign up or log in (free tier is fine)
3. Drag the **`site`** folder onto the page
4. Netlify gives you a URL like `https://random-name-123.netlify.app` — open it and confirm Skout loads
5. Bookmark that URL on your phone (Add to Home Screen for an app icon)

**Optional:** In Netlify → Site configuration → Site details → Change site name → pick something like `skout-gardner` so the URL is `https://skout-gardner.netlify.app`.

---

## Step 3 — Repeat deploys (CLI)

After the first manual deploy, link the project once:

```bash
npm install -g netlify-cli
cd ~/free-stuff-alerts
netlify login
netlify init    # choose your existing site
```

Then every update:

```bash
./scripts/deploy.sh
```

Or:

```bash
.venv/bin/python src/main.py
netlify deploy --prod --dir=site
```

---

## Optional: remember your URL in config

After you know your Netlify URL, add it to `config/deploy.yaml`:

```yaml
public_url: https://your-site-name.netlify.app
```

The dashboard footer will show it on the next build.

---

## Custom domain later

If you ever want `skout.gildedgoosegarage.com`, see `docs/squarespace-deploy.md`. Not required for personal use.
