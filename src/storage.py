import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "seen.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS seen (posting_id TEXT PRIMARY KEY, title TEXT, url TEXT, alerted_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS image_cache (url TEXT PRIMARY KEY, image_url TEXT, fetched_at TEXT)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS listing_details (
                url TEXT PRIMARY KEY, image_url TEXT, description TEXT, fetched_at TEXT)"""
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(listing_details)")}
        if "reply_email" not in cols:
            conn.execute("ALTER TABLE listing_details ADD COLUMN reply_email TEXT DEFAULT ''")
        if "reply_url" not in cols:
            conn.execute("ALTER TABLE listing_details ADD COLUMN reply_url TEXT DEFAULT ''")


def already_seen(posting_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT 1 FROM seen WHERE posting_id = ?", (posting_id,)).fetchone()
        return row is not None


def mark_seen(posting_id: str, title: str, url: str):
    from datetime import datetime, timezone
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen (posting_id, title, url, alerted_at) VALUES (?, ?, ?, ?)",
            (posting_id, title, url, datetime.now(timezone.utc).isoformat()),
        )


def mark_seen_batch(rows: list[tuple[str, str, str]]) -> int:
    if not rows:
        return 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO seen (posting_id, title, url, alerted_at) VALUES (?, ?, ?, ?)",
            [(pid, title, url, now) for pid, title, url in rows],
        )
    return len(rows)


def get_cached_image(url: str) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT image_url FROM image_cache WHERE url = ?", (url,)
        ).fetchone()
        return row[0] if row else ""


def cache_image(url: str, image_url: str):
    from datetime import datetime, timezone
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO image_cache (url, image_url, fetched_at) VALUES (?, ?, ?)",
            (url, image_url, datetime.now(timezone.utc).isoformat()),
        )


def get_cached_details(url: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT image_url, description, reply_email, reply_url FROM listing_details WHERE url = ?",
            (url,),
        ).fetchone()
        if row:
            return {
                "image_url": row[0] or "",
                "description": row[1] or "",
                "reply_email": row[2] or "" if len(row) > 2 else "",
                "reply_url": row[3] or "" if len(row) > 3 else "",
            }
    img = get_cached_image(url)
    return {"image_url": img, "description": "", "reply_email": "", "reply_url": ""}


def cache_details(
    url: str,
    image_url: str,
    description: str,
    reply_email: str = "",
    reply_url: str = "",
):
    from datetime import datetime, timezone
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO listing_details
               (url, image_url, description, reply_email, reply_url, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (url, image_url, description, reply_email, reply_url, datetime.now(timezone.utc).isoformat()),
        )
        if image_url:
            conn.execute(
                "INSERT OR REPLACE INTO image_cache (url, image_url, fetched_at) VALUES (?, ?, ?)",
                (url, image_url, datetime.now(timezone.utc).isoformat()),
            )
