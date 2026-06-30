# GitHub Pages — share Skout with friends

Skout builds a static dashboard (`site/`). You run scans on your Mac; friends open a public URL — no login, no repo access.

## One-time setup

1. **Create repo** — [github.com/GildedGooseltd/Auto-Skout](https://github.com/GildedGooseltd/Auto-Skout) (already created).

2. **Link remote** (from `free-stuff-alerts`):
   ```bash
   git remote add origin git@github.com:GildedGooseltd/Auto-Skout.git
   git push -u origin main
   ```

3. **Enable Pages** — repo **Settings → Pages**:
   - **Build and deployment** → Source: **Deploy from a branch**
   - Branch: **`gh-pages`** · folder **`/ (root)`** → **Save**

4. **Facebook session** (optional, for FB listings):
   ```bash
   .venv/bin/python src/scrapers/facebook.py --login
   .venv/bin/playwright install chromium
   ```

## GitHub login (do this once)

Skout scripts **do not** keep your token in the git remote URL (on purpose — safer). After one successful push they save it to **macOS Keychain** so plain `git push` works later.

1. Create a token: [github.com/settings/tokens/new?scopes=repo](https://github.com/settings/tokens/new?scopes=repo)
2. Add to `.env` in the project root (never commit this file):

   ```bash
   GITHUB_TOKEN=ghp_your_token_here
   ```

3. Push once:

   ```bash
   ./scripts/first-push.sh
   ```

After that, `./scripts/push-pages-only.sh gardner-farm` should work without re-pasting.

**If it still asks:** the token may be expired, missing `repo` scope, or for the wrong GitHub account. Create a new classic token with **repo** checked.

## Publish (each time you want fresh results)

```bash
# Full scan + publish
./scripts/publish-github.sh kate-vehicles

# Or push existing site/ only (fast — fixes 404 if gh-pages missing)
./scripts/push-pages-only.sh
```

Paste token when prompted, or: `./scripts/push-pages-only.sh ghp_YOUR_TOKEN`

**One-time Pages setup** (required or URL shows README, not trucks):

https://github.com/GildedGooseltd/Auto-Skout/settings/pages

| Setting | Value |
|---------|--------|
| Source | **Deploy from a branch** (not main/README) |
| Branch | **`gh-pages`** |
| Folder | **`/ (root)`** |

If you see the README (“Quick start”, bash blocks) instead of listing tiles, Pages is on **`main`** — switch to **`gh-pages`** above and Save.

Wait 1–2 min, then share:

**https://gildedgooseltd.github.io/Auto-Skout/**

(Replace org/repo if you used a different name.)

## What gets public

Only the **`gh-pages` branch** — `index.html` + assets + embedded listings. Your code on `main` can stay private. Secrets (`.env`, `data/facebook_state.json`) are never committed.

## Optional: show URL in dashboard footer

After first publish, set `config/deploy.yaml`:

```yaml
hosting:
  provider: github
  public_url: "https://gildedgooseltd.github.io/Auto-Skout/"
```
