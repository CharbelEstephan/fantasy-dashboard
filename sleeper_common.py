"""
Shared helpers for the Phase 2+ pull scripts (Sleeper API + Neon connection).

The original pull_sleeper.py stays self-contained; these helpers exist so the
newer scripts (player-weeks, drafts, transactions, brackets, orchestrator) don't
each re-implement the same request/retry/chain-walking logic.
"""

import os
import sys
import time

import requests
import psycopg2

import load_env  # noqa: F401  -- loads .env into os.environ on import

API = "https://api.sleeper.app/v1"
SLEEP = 0.15  # polite pause between API calls (well under 1000 req/min)
MAX_WEEK = 18


def get(path):
    """GET a Sleeper endpoint with basic retry/backoff. Returns parsed JSON or None."""
    url = f"{API}/{path}"
    for attempt in range(4):
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            time.sleep(SLEEP)
            return r.json()
        if r.status_code == 404:
            return None
        time.sleep(1.5 * (attempt + 1))
    r.raise_for_status()


def pts(whole, dec):
    """Sleeper stores season points as an integer part + a separate decimal part."""
    if whole is None:
        return None
    return float(whole) + (float(dec) / 100.0 if dec else 0.0)


def walk_chain(start_league_id):
    """Return [oldest ... newest] league dicts via previous_league_id."""
    chain, lid = [], start_league_id
    while lid:
        league = get(f"league/{lid}")
        if not league:
            break
        chain.append(league)
        lid = league.get("previous_league_id")
    chain.reverse()  # oldest season first
    return chain


def roster_owner_map(lid):
    """roster_id -> owner_id (user_id) for a league. owner_id is the stable identity."""
    rosters = get(f"league/{lid}/rosters") or []
    return {r.get("roster_id"): r.get("owner_id") for r in rosters}


def current_state():
    """/state/nfl -> dict with week, season, leg. Empty dict if unavailable."""
    return get("state/nfl") or {}


def get_conn():
    """Open a psycopg2 connection from DATABASE_URL (autocommit off)."""
    db = os.environ.get("DATABASE_URL")
    if not db:
        sys.exit("Set DATABASE_URL (in .env or the environment) first.")
    conn = psycopg2.connect(db)
    conn.autocommit = False
    return conn


# The two leagues, newest league_id per group. Chain-walking reaches prior seasons.
LEAGUES = {
    "Sundays For the Boys": "1389330278945660928",
    "Sportz Ball Boys": "1312195283332911104",
}
