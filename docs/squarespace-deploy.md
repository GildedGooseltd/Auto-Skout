# Host Skout on gildedgoosegarage.com (Squarespace)

Squarespace runs your main website but **does not let you upload custom HTML apps** like Skout. The standard fix: host Skout free on **Netlify**, then connect **`skout.gildedgoosegarage.com`** through Squarespace DNS.

---

## Step 1 — Build Skout

```bash
cd ~/free-stuff-alerts
.venv/bin/python src/main.py
```

This creates `site/index.html` and `site/data.json`.

---

## Step 2 — Deploy to Netlify (free, ~5 min)

1. Go to [app.netlify.com](https://app.netlify.com) and sign up (free).
2. Drag the **`site`** folder onto the Netlify dashboard ("Deploy manually").
3. Netlify gives you a URL like `random-name-123.netlify.app` — open it to confirm Skout works.
4. In Netlify: **Site configuration → Domain management → Add custom domain**
5. Enter: `skout.gildedgoosegarage.com`

Netlify will show DNS records you need.

---

## Step 3 — Squarespace DNS

1. Log into Squarespace → **gildedgoosegarage.com**
2. **Settings → Domains → gildedgoosegarage.com → DNS Settings**
3. Add a **Custom record**:

| Host | Type | Data |
|------|------|------|
| `skout` | CNAME | `your-site-name.netlify.app` |

(Use the exact target Netlify shows you.)

4. Save. DNS can take 15 minutes to 48 hours (usually under an hour).

---

## Step 4 — HTTPS

Netlify provisions SSL automatically once DNS propagates.

Your site: **https://skout.gildedgoosegarage.com**

Bookmark on your phone. Add to Home Screen for an app-like icon.

---

## Updating Skout after each run

**Option A — Drag & drop again**  
Rebuild (`python src/main.py`), drag `site/` folder to Netlify deploys.

**Option B — Netlify CLI (automatic)**

```bash
npm install -g netlify-cli
cd ~/free-stuff-alerts
netlify login
netlify init          # link to your site once
.venv/bin/python src/main.py
netlify deploy --prod --dir=site
```

---

## Alternative: Cloudflare Pages

Same idea — upload `site/`, add CNAME `skout` → `your-project.pages.dev` in Squarespace DNS.

---

## What stays on Squarespace

- **gildedgoosegarage.com** — your main Squarespace site (unchanged)
- **skout.gildedgoosegarage.com** — Skout only (points to Netlify)
