import requests
import csv
import sqlite3
import time
import sys
import os

# ─── Configuration ───────────────────────────────────────────────────────────
JIKAN_BASE = "https://api.jikan.moe/v4"
DELAY_BETWEEN_REQUESTS = 1.2  # Jikan rate limit: ~3 req/sec, we go slower to be safe
OUTPUT_CSV = "mal_top_anime.csv"
OUTPUT_DB = "mal_top_anime.db"


def fetch_top_anime_page(session, page):
    """Fetch a single page from Jikan top anime endpoint."""
    url = f"{JIKAN_BASE}/top/anime"
    params = {"page": page, "limit": 25}  # max 25 per page on Jikan v4

    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=30)

            if resp.status_code == 429:
                wait = 4 * (attempt + 1)
                print(f"  ⏳ Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as e:
            print(f"  ⚠ Attempt {attempt+1}/3 failed: {e}")
            time.sleep(3)

    return None


def extract_anime_data(entry):
    """Extract relevant fields from a Jikan anime entry."""
    return {
        "rank": entry.get("rank"),
        "mal_id": entry.get("mal_id"),
        "title": entry.get("title"),
        "title_english": entry.get("title_english"),
        "title_japanese": entry.get("title_japanese"),
        "type": entry.get("type"),
        "episodes": entry.get("episodes"),
        "status": entry.get("status"),
        "aired": entry.get("aired", {}).get("string"),
        "season": entry.get("season"),
        "year": entry.get("year"),
        "source": entry.get("source"),
        "duration": entry.get("duration"),
        "rating": entry.get("rating"),  # e.g., PG-13
        "score": entry.get("score"),
        "scored_by": entry.get("scored_by"),
        "members": entry.get("members"),
        "favorites": entry.get("favorites"),
        "synopsis": (entry.get("synopsis") or "")[:500],  # truncate for DB
        "genres": ", ".join(g["name"] for g in entry.get("genres", [])),
        "themes": ", ".join(t["name"] for t in entry.get("themes", [])),
        "studios": ", ".join(s["name"] for s in entry.get("studios", [])),
        "url": entry.get("url"),
        "image_url": entry.get("images", {}).get("jpg", {}).get("large_image_url"),
    }


def save_to_csv(all_anime, filepath):
    """Save to CSV file."""
    if not all_anime:
        return
    fieldnames = list(all_anime[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_anime)
    print(f"✅ CSV saved: {filepath} ({len(all_anime)} rows)")


def save_to_sqlite(all_anime, filepath):
    """Save to SQLite database."""
    if not all_anime:
        return
    conn = sqlite3.connect(filepath)
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS top_anime")
    cursor.execute("""
        CREATE TABLE top_anime (
            rank            INTEGER,
            mal_id          INTEGER PRIMARY KEY,
            title           TEXT NOT NULL,
            title_english   TEXT,
            title_japanese  TEXT,
            type            TEXT,
            episodes        INTEGER,
            status          TEXT,
            aired           TEXT,
            season          TEXT,
            year            INTEGER,
            source          TEXT,
            duration        TEXT,
            rating          TEXT,
            score           REAL,
            scored_by       INTEGER,
            members         INTEGER,
            favorites       INTEGER,
            synopsis        TEXT,
            genres          TEXT,
            themes          TEXT,
            studios         TEXT,
            url             TEXT,
            image_url       TEXT
        )
    """)

    for a in all_anime:
        cursor.execute("""
            INSERT OR REPLACE INTO top_anime VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, tuple(a.values()))

    # Create useful indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_score ON top_anime(score DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_members ON top_anime(members DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_type ON top_anime(type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_year ON top_anime(year)")

    conn.commit()
    conn.close()
    print(f"✅ SQLite DB saved: {filepath} ({len(all_anime)} rows)")


def main():
    print("=" * 60)
    print("  MAL Top Anime Fetcher (via Jikan API v4)")
    print("=" * 60)

    session = requests.Session()
    all_anime = []
    page = 1
    total_pages = None

    while True:
        status = f"📄 Page {page}"
        if total_pages:
            status += f"/{total_pages}"
        print(f"\n{status}...", end=" ", flush=True)

        data = fetch_top_anime_page(session, page)

        if data is None:
            print("❌ Failed after 3 retries. Stopping.")
            break

        entries = data.get("data", [])
        pagination = data.get("pagination", {})
        total_pages = pagination.get("last_visible_page", "?")
        has_next = pagination.get("has_next_page", False)

        if not entries:
            print("No entries. Done!")
            break

        page_anime = [extract_anime_data(e) for e in entries]
        all_anime.extend(page_anime)
        print(f"Got {len(page_anime)} anime (total: {len(all_anime)})")

        if not has_next:
            print("\n🏁 Reached the last page!")
            break

        page += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  Total anime fetched: {len(all_anime)}")
    print(f"{'=' * 60}")

    if all_anime:
        save_to_csv(all_anime, OUTPUT_CSV)
        save_to_sqlite(all_anime, OUTPUT_DB)

        # Stats
        scored = [a for a in all_anime if a["score"]]
        print(f"\n📊 Database Stats:")
        print(f"   Total entries:  {len(all_anime)}")
        if scored:
            print(f"   Score range:    {min(a['score'] for a in scored):.2f} — {max(a['score'] for a in scored):.2f}")

        # Type breakdown
        types = {}
        for a in all_anime:
            t = a["type"] or "Unknown"
            types[t] = types.get(t, 0) + 1
        print(f"   Type breakdown: {', '.join(f'{t}: {c}' for t, c in sorted(types.items(), key=lambda x: -x[1]))}")

        # Top 10
        print(f"\n🏆 Top 10:")
        print(f"  {'Rank':<6} {'Score':<7} {'Type':<8} {'Title'}")
        print(f"  {'-'*65}")
        for a in all_anime[:10]:
            s = f"{a['score']:.2f}" if a['score'] else "N/A"
            print(f"  {a['rank'] or '?':<6} {s:<7} {a['type'] or '?':<8} {a['title']}")

        print(f"\n📁 Output files:")
        print(f"   • {os.path.abspath(OUTPUT_CSV)}")
        print(f"   • {os.path.abspath(OUTPUT_DB)}")

        print(f"\n💡 Example queries:")
        print(f'   sqlite3 {OUTPUT_DB} "SELECT rank, title, score FROM top_anime WHERE type=\'Movie\' LIMIT 10;"')
        print(f'   sqlite3 {OUTPUT_DB} "SELECT genres, COUNT(*) c FROM top_anime GROUP BY genres ORDER BY c DESC LIMIT 10;"')
    else:
        print("\n❌ No data fetched.")


if __name__ == "__main__":
    main()
