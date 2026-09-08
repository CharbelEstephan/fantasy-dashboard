#!/usr/bin/env python3
"""
Phase 2 — Player-week (bench data).

For each season/week of both leagues, read the matchups and write one
`player_week` row per player on every team's roster that week: starters and
bench alike. Points come from the matchup's `players_points` dict, and
`is_starter = player_id in starters`.

Bench points = sum(points) where NOT is_starter. This is the biggest table
(~17k rows across all seasons) and powers the bench-management stats.

Null guards (per the spec's gotchas): a week's matchups list can be empty/None,
and an individual team's `players` / `players_points` can be null in bye or
consolation weeks -- those are skipped cleanly.

    python pull_player_weeks.py
"""

from psycopg2.extras import execute_values

from sleeper_common import get, walk_chain, roster_owner_map, get_conn, LEAGUES, MAX_WEEK


def ensure_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS player_week (
            league_id   TEXT NOT NULL REFERENCES seasons(league_id),
            season      INT  NOT NULL,
            week        INT  NOT NULL,
            roster_id   INT  NOT NULL,
            user_id     TEXT REFERENCES managers(user_id),
            player_id   TEXT NOT NULL,
            points      NUMERIC(7,2),
            is_starter  BOOLEAN NOT NULL,
            PRIMARY KEY (league_id, week, roster_id, player_id)
        )
    """)


def load_season(cur, league):
    lid = league["league_id"]
    season = int(league.get("season") or 0)
    roster_owner = roster_owner_map(lid)

    season_rows = 0
    for week in range(1, MAX_WEEK + 1):
        matchups = get(f"league/{lid}/matchups/{week}")
        if not matchups:
            continue

        rows = []
        for m in matchups:
            rid = m.get("roster_id")
            starters = set(m.get("starters") or [])
            players = m.get("players")
            if not players:
                continue  # bye / empty slot -- nothing to record
            if not m.get("points"):
                continue  # team hasn't played this week (Sleeper pre-fills 0.0) -- skip
            ppts = m.get("players_points") or {}
            uid = roster_owner.get(rid)
            for pid in players:
                if pid is None:
                    continue  # empty roster slot
                pt = ppts.get(pid)
                rows.append((
                    lid, season, week, rid, uid, pid,
                    pt, pid in starters,
                ))

        if rows:
            execute_values(cur, """
                INSERT INTO player_week
                    (league_id, season, week, roster_id, user_id,
                     player_id, points, is_starter)
                VALUES %s
                ON CONFLICT (league_id, week, roster_id, player_id) DO UPDATE SET
                    season     = EXCLUDED.season,
                    user_id    = EXCLUDED.user_id,
                    points     = EXCLUDED.points,
                    is_starter = EXCLUDED.is_starter
            """, rows, page_size=1000)
            season_rows += len(rows)

    print(f"    season {season} ({lid}): {season_rows} player-week rows")
    return season_rows


def main():
    conn = get_conn()
    cur = conn.cursor()
    ensure_schema(cur)
    conn.commit()

    grand_total = 0
    for group, start_id in LEAGUES.items():
        chain = walk_chain(start_id)
        if not chain:
            print(f"[{group}] no league found for id {start_id}")
            continue
        seasons = ", ".join(l.get("season", "?") for l in chain)
        print(f"[{group}] {len(chain)} season(s): {seasons}")
        for league in chain:
            grand_total += load_season(cur, league)
        conn.commit()
        print(f"[{group}] committed.")

    cur.close()
    conn.close()
    print(f"Done. {grand_total} player-week rows total.")


if __name__ == "__main__":
    main()
