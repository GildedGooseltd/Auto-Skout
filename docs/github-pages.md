# GitHub Pages — Skout vs Auto Skout

One repo (**Auto-Skout**), **two separate apps** on different URLs. Publishing one does not overwrite the other.

| App | Profile | Live URL |
|-----|---------|----------|
| **Skout** | `gardner-farm` | https://gildedgooseltd.github.io/Auto-Skout/skout/ |
| **Auto Skout** | `kate-vehicles` | https://gildedgooseltd.github.io/Auto-Skout/auto-skout/ |
| **Hub** | — | https://gildedgooseltd.github.io/Auto-Skout/ |

---

## Steps to fix (one-time)

### 1. GitHub token in `.env`

```bash
GITHUB_TOKEN=ghp_your_token_here
```

Create at [github.com/settings/tokens/new?scopes=repo](https://github.com/settings/tokens/new?scopes=repo)

### 2. Push code to `main`

```bash
cd ~/free-stuff-alerts
./scripts/first-push.sh
```

### 3. Enable GitHub Pages

https://github.com/GildedGooseltd/Auto-Skout/settings/pages

- **Deploy from a branch**
- Branch: **`gh-pages`**
- Folder: **`/ (root)`**
- Save

### 4. Publish **each** app once

```bash
# Skout (farm / free stuff)
./scripts/publish-github.sh gardner-farm

# Auto Skout (vehicles) — separate folder, won't clobber Skout
./scripts/publish-github.sh kate-vehicles
```

Or fast publish from an existing local build:

```bash
./scripts/push-pages-only.sh --no-build gardner-farm
./scripts/push-pages-only.sh --no-build kate-vehicles
```

Wait 1–2 minutes, then open the hub: https://gildedgooseltd.github.io/Auto-Skout/

---

## Day-to-day

| You want… | Command |
|-----------|---------|
| Refresh **Skout** only | `./scripts/publish-github.sh gardner-farm` |
| Refresh **Auto Skout** only | `./scripts/publish-github.sh kate-vehicles` |
| Local scan, no publish | `SKOUT_PROFILE=gardner-farm .venv/bin/python src/main.py --all --open` |

---

## What gets public

Only **`gh-pages`**: static HTML + embedded listings per app folder. Secrets (`.env`, Facebook session) stay on your Mac.

Optional footer URL in `config/deploy.yaml`:

```yaml
hosting:
  provider: github
  public_url: "https://gildedgooseltd.github.io/Auto-Skout/skout/"
```

---

## Facebook session (optional)

```bash
.venv/bin/python src/scrapers/facebook.py --login
.venv/bin/playwright install chromium
```
