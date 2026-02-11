import requests
from bs4 import BeautifulSoup
import csv
import sqlite3
import time
import re
import sys
import os

# ─── Configuration ───────────────────────────────────────────────────────────
BASE_URL = "https://myanimelist.net/topanime.php"
DELAY_BETWEEN_REQUESTS = 2  # seconds (be respectful to the server)
OUTPUT_CSV = "mal_top_anime.csv"
OUTPUT_DB = "mal_top_anime.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def parse_anime_row(row):
    """Parse a single anime row from the ranking table."""
    anime = {}

    # Rank
    rank_tag = row.select_one("td.rank span")
    if rank_tag:
        anime["rank"] = int(rank_tag.text.strip())
    else:
        return None

    # Title & URL
    title_tag = row.select_one("td.title a.hoverinfo_trigger")
    if title_tag:
        anime["title"] = title_tag.text.strip()
        anime["url"] = title_tag["href"]
        # Extract anime ID from URL
        match = re.search(r"/anime/(\d+)/", anime["url"])
        anime["mal_id"] = int(match.group(1)) if match else None
    else:
        return None

    # Info text (type, episodes, dates)
    info_tag = row.select_one("td.title div.information")
    if info_tag:
        info_text = info_tag.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in info_text.split("\n") if l.strip()]

        # Line 1: Type (eps)
        if lines:
            type_match = re.match(r"(\w+)\s*\((\d+)\s*eps?\)", lines[0])
            if type_match:
                anime["type"] = type_match.group(1)
                anime["episodes"] = int(type_match.group(2))
            else:
                type_only = re.match(r"(\w+)", lines[0])
                anime["type"] = type_only.group(1) if type_only else lines[0]
                eps_match = re.search(r"\((\d+)\s*eps?\)", lines[0])
                anime["episodes"] = int(eps_match.group(1)) if eps_match else None

        # Line 2: Airing dates
        if len(lines) > 1:
            anime["aired"] = lines[1]
        else:
            anime["aired"] = None

        # Line 3: Members
        if len(lines) > 2:
            members_match = re.search(r"([\d,]+)\s*members", lines[2])
            if members_match:
                anime["members"] = int(members_match.group(1).replace(",", ""))
            else:
                anime["members"] = None
        else:
            anime["members"] = None
    else:
        anime["type"] = None
        anime["episodes"] = None
        anime["aired"] = None
        anime["members"] = None

    # Score
    score_tag = row.select_one("td.score span")
    if score_tag:
        score_text = score_tag.text.strip()
        try:
            anime["score"] = float(score_text)
        except ValueError:
            anime["score"] = None
    else:
        anime["score"] = None

    return anime


def scrape_page(session, limit):
    """Scrape a single page of top anime results."""
    params = {"limit": limit} if limit > 0 else {}
    try:
        response = session.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"  ⚠ Request failed for limit={limit}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.select("tr.ranking-list")

    anime_list = []
    for row in rows:
        anime = parse_anime_row(row)
        if anime:
            anime_list.append(anime)

    return anime_list


def save_to_csv(all_anime, filepath):
    """Save anime list to CSV."""
    if not all_anime:
        return
    fieldnames = ["rank", "mal_id", "title", "type", "episodes", "aired",
                  "members", "score", "url"]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_anime)
    print(f"\n✅ CSV saved: {filepath}")


def save_to_sqlite(all_anime, filepath):
    """Save anime list to SQLite database."""
    if not all_anime:
        return
    conn = sqlite3.connect(filepath)
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS top_anime")
    cursor.execute("""
        CREATE TABLE top_anime (
            rank        INTEGER PRIMARY KEY,
            mal_id      INTEGER UNIQUE,
            title       TEXT NOT NULL,
            type        TEXT,
            episodes    INTEGER,
            aired       TEXT,
            members     INTEGER,
            score       REAL,
            url         TEXT
        )
    """)

    for anime in all_anime:
        cursor.execute("""
            INSERT OR REPLACE INTO top_anime
                (rank, mal_id, title, type, episodes, aired, members, score, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            anime["rank"], anime["mal_id"], anime["title"],
            anime["type"], anime["episodes"], anime["aired"],
            anime["members"], anime["score"], anime["url"],
        ))

    conn.commit()
    conn.close()
    print(f"✅ SQLite DB saved: {filepath}")


def main():
    print("=" * 60)
    print("  MyAnimeList Top Anime Scraper")
    print("=" * 60)

    all_anime = []
    limit = 0
    page_num = 1
    consecutive_failures = 0

    session = requests.Session()

    while True:
        print(f"\n📄 Page {page_num} (limit={limit})...", end=" ", flush=True)
        anime_list = scrape_page(session, limit)

        if not anime_list:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                print("\n⛔ No more results (3 consecutive empty pages). Done!")
                break
            print(f"Empty page. Retrying... ({consecutive_failures}/3)")
            time.sleep(DELAY_BETWEEN_REQUESTS * 2)
            continue

        consecutive_failures = 0
        all_anime.extend(anime_list)
        print(f"Got {len(anime_list)} anime (total: {len(all_anime)})")

        # If fewer than 50 results, we've reached the last page
        if len(anime_list) < 50:
            print("\n🏁 Reached the last page!")
            break

        limit += 50
        page_num += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  Total anime scraped: {len(all_anime)}")
    print(f"{'=' * 60}")

    if all_anime:
        save_to_csv(all_anime, OUTPUT_CSV)
        save_to_sqlite(all_anime, OUTPUT_DB)

        # Print a sample
        print(f"\n📊 Top 10 Preview:")
        print(f"{'Rank':<6} {'Score':<7} {'Type':<8} {'Title'}")
        print("-" * 70)
        for a in all_anime[:10]:
            score = f"{a['score']:.2f}" if a['score'] else "N/A"
            print(f"{a['rank']:<6} {score:<7} {a['type'] or '?':<8} {a['title']}")

        print(f"\n📁 Files created:")
        print(f"   • {os.path.abspath(OUTPUT_CSV)}")
        print(f"   • {os.path.abspath(OUTPUT_DB)}")
    else:
        print("\n❌ No anime data scraped. MAL may be blocking requests.")
        print("   Try again later or use the Jikan API alternative (see below).")

    print(f"\n💡 Tip: To query the database:")
    print(f'   sqlite3 {OUTPUT_DB} "SELECT rank, title, score FROM top_anime LIMIT 20;"')


if __name__ == "__main__":
    main()
