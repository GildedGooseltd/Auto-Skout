from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "skout.html"

TIER_STYLES = {
    "urgent": ("#dc2626", "Within 5 mi"),
    "paid_wanted": ("#b45309", "In budget"),
    "near_you": ("#15803d", "Near you"),
    "worth_the_drive": ("#1d4ed8", "Worth drive"),
    "everything_else": ("#57534e", "Match"),
}


def write_dashboard(
    items: list[tuple],
    loc: dict,
    cfg: dict,
    *,
    total_checked: int,
    new_count: int,
) -> Path:
    app = cfg["profile"].get("app_name", "Skout")
    template = escape(cfg["scoring"].get("response_template", "").strip())
    loc_label = f"{loc.get('city', 'Gardner')}, {loc.get('state', 'CO')} {loc.get('zip', '')}"
    now = datetime.now().strftime("%a %b %-d, %-I:%M %p")

    by_category: dict[str, list] = defaultdict(list)
    by_platform: dict[str, int] = defaultdict(int)
    for listing, score, tier in items:
        by_category[listing.category_id].append((listing, score, tier))
        by_platform[listing.platform_label] += 1

    # Category legend chips
    cat_meta = {}
    for listing, _, _ in items:
        cat_meta[listing.category_id] = (listing.category_icon, listing.category_label)
    chips = []
    for cid, (icon, label) in sorted(cat_meta.items(), key=lambda x: x[1][1]):
        count = len(by_category[cid])
        chips.append(
            f'<a class="chip" href="#cat-{cid}">{icon} {escape(label)} ({count})</a>'
        )
    chip_row = " ".join(chips) if chips else ""

    plat_row = " · ".join(
        f"{escape(k)} {v}" for k, v in sorted(by_platform.items())
    )

    sections = []
    for cid in sorted(by_category.keys(), key=lambda c: cat_meta.get(c, ("", c))[1]):
        icon, label = cat_meta[cid]
        cards = []
        for listing, score, tier in sorted(by_category[cid], key=lambda x: -x[1]):
            color, _ = TIER_STYLES.get(tier, ("#57534e", tier))
            cards.append(
                f"""
                <article class="card" style="border-left: 4px solid {color}">
                  <div class="card-inner">
                    <div class="card-main">
                      <div class="meta">
                        <span class="plat">{listing.platform_icon} {escape(listing.platform_label)}</span>
                        <span class="badge" style="background:{color}">{escape(tier.replace('_', ' '))}</span>
                        <span class="score">{score}</span>
                      </div>
                      <h3><a href="{escape(listing.url)}" target="_blank" rel="noopener">{escape(listing.title)}</a></h3>
                      <p class="detail">{escape(listing.price)} · {escape(listing.location)}</p>
                      <details>
                        <summary>Copy pickup message</summary>
                        <textarea readonly onclick="this.select()">{template}</textarea>
                      </details>
                    </div>
                    <div class="cat-icon" title="{escape(label)}">{icon}</div>
                  </div>
                </article>"""
            )
        sections.append(
            f'<section id="cat-{cid}" class="cat-section">'
            f'<h2 class="cat-heading">{icon} {escape(label)}</h2>'
            + "\n".join(cards)
            + "</section>"
        )

    body = "\n".join(sections) if sections else '<p class="empty">No matches this run.</p>'

    setup_notes = """
    <aside class="setup">
      <strong>More sources:</strong>
      ♻️ Freecycle — join groups at freecycle.org (runs on full scan, not --test).
      📘 Facebook — run <code>.venv/bin/python src/scrapers/facebook.py --login</code> once, then
      <code>pip install playwright && playwright install chromium</code>.
    </aside>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(app)} — {escape(loc_label)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0;
      background: #f5f0e8; color: #1c1917; }}
    header {{ background: linear-gradient(135deg, #3f6212, #166534); color: #fff;
      padding: 1.5rem 1.25rem; }}
    header h1 {{ margin: 0 0 .25rem; font-size: 1.75rem; }}
    header p {{ margin: .2rem 0; opacity: .9; font-size: .95rem; }}
    main {{ max-width: 760px; margin: 0 auto; padding: 1rem; }}
    .stats {{ display: flex; gap: .75rem; flex-wrap: wrap; margin-bottom: .75rem; }}
    .stat {{ background: #fff; border-radius: 10px; padding: .75rem 1rem;
      box-shadow: 0 1px 3px rgba(0,0,0,.08); flex: 1; min-width: 100px; }}
    .stat strong {{ display: block; font-size: 1.4rem; color: #166534; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: 1rem; }}
    .chip {{ background: #fff; border-radius: 999px; padding: .35rem .75rem;
      font-size: .8rem; text-decoration: none; color: #1c1917;
      box-shadow: 0 1px 2px rgba(0,0,0,.06); }}
    .chip:hover {{ background: #dcfce7; }}
    .cat-section {{ margin-bottom: 1.5rem; }}
    .cat-heading {{ font-size: 1.1rem; margin: 0 0 .6rem; color: #3f6212; }}
    .card {{ background: #fff; border-radius: 10px; padding: 0;
      margin-bottom: .65rem; box-shadow: 0 1px 3px rgba(0,0,0,.08); overflow: hidden; }}
    .card-inner {{ display: flex; align-items: stretch; }}
    .card-main {{ flex: 1; padding: .85rem 1rem; min-width: 0; }}
    .cat-icon {{ font-size: 2rem; padding: .85rem 1rem; background: #f5f5f4;
      display: flex; align-items: center; border-left: 1px solid #e7e5e4; min-width: 4rem;
      justify-content: center; }}
    .meta {{ display: flex; gap: .45rem; align-items: center; flex-wrap: wrap; margin-bottom: .4rem; }}
    .plat {{ font-size: .75rem; color: #57534e; }}
    .badge {{ color: #fff; font-size: .65rem; text-transform: uppercase;
      padding: .15rem .45rem; border-radius: 4px; }}
    .score {{ color: #a8a29e; font-size: .8rem; }}
    .card h3 {{ margin: 0 0 .3rem; font-size: 1rem; line-height: 1.35; }}
    .card h3 a {{ color: #1c1917; text-decoration: none; }}
    .card h3 a:hover {{ text-decoration: underline; }}
    .detail {{ margin: 0; color: #57534e; font-size: .85rem; }}
    textarea {{ width: 100%; margin-top: .4rem; min-height: 3.5rem; font-size: .8rem;
      padding: .4rem; border: 1px solid #d6d3d1; border-radius: 6px; }}
    .empty {{ text-align: center; color: #78716c; padding: 2rem; }}
    .setup {{ background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;
      padding: .75rem 1rem; font-size: .85rem; margin-bottom: 1rem; color: #78350f; }}
    .setup code {{ background: #fef3c7; padding: .1rem .3rem; border-radius: 3px; font-size: .8rem; }}
    footer {{ text-align: center; color: #a8a29e; font-size: .8rem; padding: 2rem; }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(app)}</h1>
    <p>{escape(loc_label)} · {now}</p>
    <p>{escape(plat_row) if plat_row else "No sources yet"}</p>
  </header>
  <main>
    <div class="stats">
      <div class="stat"><strong>{new_count}</strong>new</div>
      <div class="stat"><strong>{total_checked}</strong>checked</div>
      <div class="stat"><strong>{len(items)}</strong>showing</div>
    </div>
    {setup_notes}
    <div class="chips">{chip_row}</div>
    {body}
  </main>
  <footer>Skout refreshes this page each run · Bookmark it</footer>
</body>
</html>"""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    return OUTPUT
