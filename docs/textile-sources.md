# Textile & sewing machine channels (Tier 2 + Tier 3)

Skout can scan **industrial sewing / ultrasonic / textile** sources alongside Craigslist and estate sales. Turn off **Free only** — almost all machines are paid listings.

---

## Tier 2 — Marketplaces & auctions

| Source | Config key | Where to look |
|--------|------------|---------------|
| **Machinio** | `textile_marketplaces` → `machinio` | [machinio.com/cat/industrial-sewing-machines](https://www.machinio.com/cat/industrial-sewing-machines) |
| **eBay** (local pickup) | `textile_marketplaces` → `ebay` | eBay search near zip 81040 |
| **HGP Auctions** | `textile_auctions` → `hgp` | [hgpauction.com/textiles-apparel](https://www.hgpauction.com/auction-category/textiles-apparel/) |
| **GovPlanet** | `textile_auctions` → `govplanet` | Surplus textiles category |
| **GovDeals** | `textile_auctions` → `govdeals` | CO state surplus search |
| **IRS Auctions** | `textile_auctions` → `irs` | Plant liquidation events |
| **EstateSales.org** | `estate_sales` → `estatesales_org` | Same liquidator network as .net |
| **GSALR** | `estate_sales` → `gsalr` | [gsalr.com/garage-sales-{city}.html](https://gsalr.com) |

**Note:** Machinio, eBay, and GovDeals often block automated browsers (HTTP 403). Scans from your Mac usually work better than cloud agents. **Pleasant Street Machinery** (Tier 3) mirrors much Machinio dealer stock and uses a sitemap (more reliable).

---

## Tier 3 — Dealer inventory

| Source | Config key | Site |
|--------|------------|------|
| **Pleasant Street Machinery** | `dealer_inventory` → `pleasant_street` | [pleasantstmachinery.com](https://www.pleasantstmachinery.com/industrial-sewing-machines) |
| **MD Equipment Services** | `dealer_inventory` → `md_equipment` | Sonobond / ultrasonic welders |
| **Cutsew.com** | `dealer_inventory` → `cutsew` | Used Juki / industrial stock |

---

## Setup (`profiles/gardner-farm/sources.yaml`)

Already enabled by default:

```yaml
textile_marketplaces:
  enabled: true
  sites: [machinio, ebay]
  search_terms: [industrial sewing machine, ultrasonic sewing machine, ...]

textile_auctions:
  enabled: true
  sites: [hgp, govplanet, govdeals, irs]

dealer_inventory:
  enabled: true
  sites: [pleasant_street, md_equipment, cutsew]

estate_sales:
  sites: [estatesales_net, estatesales_org, gsalr]
  match_keywords: [..., sewing, serger, textile, ultrasonic]
```

**Requires Playwright** for marketplaces, auctions, estate.org, and GSALR:

```bash
.venv/bin/playwright install chromium
```

**Scan:**

```bash
SKOUT_PROFILE=gardner-farm .venv/bin/python src/main.py --all --open
```

Filter by **Machinio · eBay**, **Textile auctions**, or **Dealer inventory** in the Source sidebar.

---

## Tips

- Use quick search preset **Sewing** in the dashboard for textile keyword focus.
- Pleasant Street listings are national (often IL/PA) — still useful for comps and rare models.
- HGP auctions are event-based — titles mention “cut & sew”, “apparel”, “uniforms”.
- Estate/GSALR sales match via photo captions — increase `max_detail_fetches` for more coverage.
