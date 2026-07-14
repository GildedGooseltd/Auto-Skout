# Estate & yard sales

Skout can pull **estate sales, moving sales, and multi-day liquidations** from aggregator sites — not just free classifieds. Listings include **sale dates, address, photo galleries, and item text** (including photo captions like “John Deere garden tractor”).

Turn off **Free only** in the dashboard to see estate-sale hits (they are paid events, not $0 listings).

---

## Enabled now: EstateSales.net

**Site:** [estatesales.net](https://www.estatesales.net) — largest US estate-sale calendar (since 2002). Professional liquidators post multi-photo sales with item-level captions.

**How Skout matches your hunt:**

1. Browse configured Colorado cities (Pueblo, Springs, Denver, etc.)
2. Open each sale detail page
3. Match `match_keywords` against **title + description + photo alt text**
4. Keep sales that mention tools, garden/farm gear, freezers, building materials, paint, etc.

**Setup** (`profiles/gardner-farm/sources.yaml`):

```yaml
estate_sales:
  enabled: true
  sites: [estatesales_net]
  require_keyword_match: true
  max_sales_per_city: 25
  max_detail_fetches: 50
  match_keywords: [tool, garden, farm, tractor, freezer, ...]
  cities:
    - name: Pueblo
      url: https://www.estatesales.net/CO/Pueblo
```

**Requires Playwright** (same as Facebook):

```bash
.venv/bin/playwright install chromium
```

**Scan:** runs with every full scan when enabled. Filter by **Estate sales** in the Source sidebar group.

---

## Sites to add next (research)

| Site | Type | Photos & text | Skout fit | Notes |
|------|------|---------------|-----------|-------|
| **[EstateSales.net](https://www.estatesales.net)** | Pro estate sales | ✅ Galleries + item captions | **Live (v1)** | Angular SPA; Playwright works |
| **[EstateSales.org](https://estatesales.org)** | Pro estate sales | ✅ Similar to above | High | GSALR partner; same liquidator network |
| **[GSALR.com](https://gsalr.com)** | Garage / yard / estate map | ✅ Photo view + list | Medium | Heavy JS; slow loads; syndicates to partners below |
| **YardSaleSearch.com** | Syndicated yard sales | ✅ | Medium | Auto-fed from GSALR posts |
| **GarageSaleFinder.com** | Syndicated | ✅ | Medium | GSALR syndication |
| **YardSales.net** | Syndicated | ✅ | Low–Med | GSALR syndication |
| **Craigslist** | Classifieds | ✅ | **Partial** | Added search terms: `estate sale`, `moving sale`, `garage sale`, `yard sale` — already in your CL scan |
| **Facebook Events** | Local events | ✅ | Medium | Needs FB login; many one-off yard sales |
| **Nextdoor** | Neighborhood | ⚠️ Variable | Low | Currently disabled in gardner-farm profile |
| **OfferUp / Marketplace** | Item listings | ✅ | Low | Individual items, not sale events |

**Recommendation:** EstateSales.net first (done), then **EstateSales.org** (similar data model), then **GSALR** for casual garage/yard sales that never hit professional sites. Craigslist terms cover one-off “estate sale” posts already in your feed.

---

## Tips

- **Free only** hides estate sales — they show dates/prices, not $0.
- Sales are **events** (Fri–Sun windows), not single items. Open the listing for directions and hours.
- Photo captions are the best signal for tools/machinery buried in a general household sale.
- Increase `max_detail_fetches` for wider keyword coverage (slower scans).
