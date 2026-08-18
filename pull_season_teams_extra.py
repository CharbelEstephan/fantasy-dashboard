#!/usr/bin/env python3
"""
Phase 6 — Extend season_teams with optimal-points + activity columns.

Adds four columns to the existing season_teams table (additive only -- no drop
or rebuild) and backfills them from each roster's settings:

    potential_points  <- ppts + ppts_decimal/100   (optimal/"potential" points)
    total_moves       <- settings.total_moves
    waiver_position   <- settings.waiver_position
    division          <- settings.division          (null if league has no divisions)

Rows are matched to the season_teams already loaded by pull_sleeper.py on
(league_id, roster_id); re-running only refreshes these four values.

    python pull_season_teams_extra.py
"""

from sleeper_common import get, walk_chain, get_conn, pts, LEAGUES


def ensure_schema(cur):
    cur.execute("ALTER TABLE season_teams ADD COLUMN IF NOT EXISTS potential_points NUMERIC(8,2)")
    cur.execute("ALTER TABLE season_teams ADD COLUMN IF NOT EXISTS total_moves INT")
    cur.execute("ALTER TABLE season_teams ADD COLUMN IF NOT EXISTS waiver_position INT")
    cur.execute("ALTER TABLE season_teams ADD COLUMN IF NOT EXISTS division INT")


def load_season(cur, league):
    lid = league["league_id"]
    rosters = get(f"league/{lid}/rosters") or []
    updated = 0
    for r in rosters:
        rid = r.get("roster_id")
        rs = r.get("settings") or {}
        cur.execute("""
            UPDATE season_teams SET
                potential_points = %s,
                total_moves      = %s,
                waiver_position  = %s,
                division         = %s
            WHERE league_id = %s AND roster_id = %s
        """, (
            pts(rs.get("ppts"), rs.get("ppts_decimal")),
            rs.get("total_moves"),
            rs.get("waiver_position"),
            rs.get("division"),
            lid, rid,
        ))
        updated += cur.rowcount
    print(f"    season {league.get('season')} ({lid}): {updated} teams updated")
    return updated


def main():
    conn = get_conn()
    cur = conn.cursor()
    ensure_schema(cur)
    conn.commit()

    total = 0
    for group, start_id in LEAGUES.items():
        chain = walk_chain(start_id)
        if not chain:
            print(f"[{group}] no league found for id {start_id}")
            continue
        seasons = ", ".join(l.get("season", "?") for l in chain)
        print(f"[{group}] {len(chain)} season(s): {seasons}")
        for league in chain:
            total += load_season(cur, league)
        conn.commit()
        print(f"[{group}] committed.")

    cur.close()
    conn.close()
    print(f"Done. {total} season_teams rows updated.")


if __name__ == "__main__":
    main()
