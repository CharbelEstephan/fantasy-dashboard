#!/usr/bin/env python3
"""
Phase 1 — NFL player reference.

Downloads Sleeper's /players/nfl dump (one ~5 MB object keyed by player_id),
caches it to a local JSON file, and loads it into the `players` table.

The dump is large and changes slowly, so we call the API at most once per day:
if a cached copy exists and is < 24h old, we reuse it instead of re-downloading.

Team defenses are keyed by team abbreviation (e.g. "PHI") rather than a numeric
id and have no full_name — those are handled gracefully.

    python pull_players.py
"""

import os
import sys
import json
import time

import requests
import psycopg2
from psycopg2.extras import execute_values

import load_env  # noqa: F401  -- loads .env into os.environ on import

API = "https://api.sleeper.app/v1"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players_nfl.json")
CACHE_MAX_AGE = 24 * 60 * 60  # seconds


def ensure_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS players (
            player_id  TEXT PRIMARY KEY,   -- numeric id, or team abbrev for defenses
            full_name  TEXT,
            position   TEXT,
            team       TEXT
        )
    """)


def load_players_dump():
    """Return the /players/nfl dict, reusing a fresh (<24h) local cache if present."""
    if os.path.exists(CACHE_FILE):
        age = time.time() - os.path.getmtime(CACHE_FILE)
        if age < CACHE_MAX_AGE:
            print(f"Using cached {os.path.basename(CACHE_FILE)} "
                  f"({age / 3600:.1f}h old).")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    print("Downloading /players/nfl (~5 MB)...")
    r = requests.get(f"{API}/players/nfl", timeout=60)
    r.raise_for_status()
    data = r.json()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"Cached {len(data)} players to {os.path.basename(CACHE_FILE)}.")
    return data


def player_name(pid, p):
    """Best-effort display name. Defenses lack full_name -> synthesize one."""
    name = p.get("full_name")
    if name:
        return name
    # team defenses: player_id is the team abbrev, position is "DEF"
    if (p.get("position") == "DEF") or (not str(pid).isdigit()):
        team = p.get("team") or pid
        return f"{team} DEF"
    # last resort: first/last if present
    parts = [p.get("first_name"), p.get("last_name")]
    joined = " ".join(x for x in parts if x)
    return joined or None


def main():
    db = os.environ.get("DATABASE_URL")
    if not db:
        sys.exit("Set DATABASE_URL (in .env or the environment) first.")

    data = load_players_dump()

    rows = []
    for pid, p in data.items():
        if not isinstance(p, dict):
            continue
        rows.append((
            pid,
            player_name(pid, p),
            p.get("position"),
            p.get("team"),
        ))

    conn = psycopg2.connect(db)
    conn.autocommit = False
    cur = conn.cursor()
    ensure_schema(cur)

    execute_values(cur, """
        INSERT INTO players (player_id, full_name, position, team)
        VALUES %s
        ON CONFLICT (player_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            position  = EXCLUDED.position,
            team      = EXCLUDED.team
    """, rows, page_size=1000)

    conn.commit()
    cur.execute("SELECT count(*) FROM players")
    total = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"Loaded {len(rows)} players (table now has {total}).")


if __name__ == "__main__":
    main()
