#!/usr/bin/env python3
"""
Pull Sleeper league history (head-to-head + scoring) into Neon Postgres.

For each league below, it walks the previous_league_id chain back to the
first Sleeper season, then loads users, rosters, and weekly matchups.

Setup:
    pip install requests psycopg2-binary
    export DATABASE_URL="postgresql://...your-neon-connection-string..."
    python pull_sleeper.py

Re-running is safe: everything upserts, so run it weekly during the live
season to pick up new games without duplicating anything.
"""

import os
import sys
import time
import requests
import psycopg2
from psycopg2.extras import execute_values

import load_env  # noqa: F401  -- loads .env into os.environ on import

API = "https://api.sleeper.app/v1"

# Current-season league_id for each of your leagues. The script chains
# backward from here, so you only need the newest id per league.
# Add your second league once you have its id.
LEAGUES = {
    "Sundays For the Boys": "1389330278945660928",
    "Sportz Ball Boys": "1312195283332911104",
}

MAX_WEEK = 18  # upper bound; weeks with no matchup data are skipped


def get(path):
    """GET a Sleeper endpoint with basic retry/backoff. Returns parsed JSON."""
    url = f"{API}/{path}"
    for attempt in range(4):
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
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


def load_season(cur, group, league):
    lid = league["league_id"]
    season = int(league.get("season") or 0)
    settings = league.get("settings") or {}
    playoff_start = settings.get("playoff_week_start")

    cur.execute("""
        INSERT INTO seasons (league_id, league_group, season, name,
                             previous_league_id, playoff_week_start,
                             total_rosters, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (league_id) DO UPDATE SET
            league_group = EXCLUDED.league_group,
            season = EXCLUDED.season,
            name = EXCLUDED.name,
            previous_league_id = EXCLUDED.previous_league_id,
            playoff_week_start = EXCLUDED.playoff_week_start,
            total_rosters = EXCLUDED.total_rosters,
            status = EXCLUDED.status
    """, (lid, group, season, league.get("name"),
          league.get("previous_league_id"), playoff_start,
          league.get("total_rosters"), league.get("status")))

    # users -> managers
    users = get(f"league/{lid}/users") or []
    user_name = {}
    for u in users:
        uid = u.get("user_id")
        meta = u.get("metadata") or {}
        name = u.get("display_name") or meta.get("team_name") or uid
        user_name[uid] = name
        cur.execute("""
            INSERT INTO managers (user_id, display_name)
            VALUES (%s,%s)
            ON CONFLICT (user_id) DO UPDATE SET display_name = EXCLUDED.display_name
        """, (uid, name))

    # rosters -> season_teams, and roster_id -> owner map for matchups
    rosters = get(f"league/{lid}/rosters") or []
    roster_owner = {}
    for r in rosters:
        rid = r.get("roster_id")
        oid = r.get("owner_id")
        roster_owner[rid] = oid
        rs = r.get("settings") or {}
        meta = r.get("metadata") or {}
        team_name = meta.get("team_name") or user_name.get(oid)
        cur.execute("""
            INSERT INTO season_teams (league_id, roster_id, user_id, team_name,
                wins, losses, ties, points_for, points_against)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (league_id, roster_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                team_name = EXCLUDED.team_name,
                wins = EXCLUDED.wins,
                losses = EXCLUDED.losses,
                ties = EXCLUDED.ties,
                points_for = EXCLUDED.points_for,
                points_against = EXCLUDED.points_against
        """, (lid, rid, oid, team_name,
              rs.get("wins"), rs.get("losses"), rs.get("ties"),
              pts(rs.get("fpts"), rs.get("fpts_decimal")),
              pts(rs.get("fpts_against"), rs.get("fpts_against_decimal"))))

    # matchups per week -> team_week
    for week in range(1, MAX_WEEK + 1):
        matchups = get(f"league/{lid}/matchups/{week}")
        if not matchups:
            continue
        is_playoff = bool(playoff_start and week >= playoff_start)
        rows = []
        for m in matchups:
            rid = m.get("roster_id")
            score = m.get("points")
            if not score:
                continue  # unplayed (Sleeper pre-fills future weeks at 0.0) / empty slot
            rows.append((lid, season, week, m.get("matchup_id"), rid,
                         roster_owner.get(rid), score, is_playoff))
        if rows:
            execute_values(cur, """
                INSERT INTO team_week (league_id, season, week, matchup_id,
                    roster_id, user_id, points, is_playoff)
                VALUES %s
                ON CONFLICT (league_id, week, roster_id) DO UPDATE SET
                    matchup_id = EXCLUDED.matchup_id,
                    user_id = EXCLUDED.user_id,
                    points = EXCLUDED.points,
                    is_playoff = EXCLUDED.is_playoff
            """, rows)


def main():
    db = os.environ.get("DATABASE_URL")
    if not db:
        sys.exit("Set DATABASE_URL to your Neon connection string first.")

    conn = psycopg2.connect(db)
    conn.autocommit = False
    cur = conn.cursor()

    for group, start_id in LEAGUES.items():
        if "PUT_SECOND" in start_id:
            continue
        chain = walk_chain(start_id)
        if not chain:
            print(f"[{group}] no league found for id {start_id}")
            continue
        seasons = ", ".join(l.get("season", "?") for l in chain)
        print(f"[{group}] found {len(chain)} season(s): {seasons}")
        for league in chain:
            load_season(cur, group, league)
        conn.commit()
        print(f"[{group}] committed.")

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()