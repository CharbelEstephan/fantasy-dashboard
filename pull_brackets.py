#!/usr/bin/env python3
"""
Phase 5 — Playoff brackets.

For each season of both leagues, pull the winners and losers brackets and write
them to `playoff_matchups`. Each bracket row has:
    r (round), m (match id), t1/t2 (roster_ids), w (winner), l (loser),
    p (placement).

Guard: t1/t2 can be a plain roster_id (int) OR a reference object like
{"w": 3} / {"l": 3} meaning "winner/loser of match 3" -- those references are
stored as NULL roster_ids (the roster isn't decided yet in the source data).
w / l / p can also be null for unplayed matches or non-placement games.

    python pull_brackets.py
"""

from psycopg2.extras import execute_values

from sleeper_common import get, walk_chain, get_conn, LEAGUES


def ensure_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS playoff_matchups (
            league_id         TEXT NOT NULL REFERENCES seasons(league_id),
            season            INT  NOT NULL,
            bracket           TEXT NOT NULL,     -- 'winners' / 'losers'
            round             INT,
            match_id          INT,
            roster_id_1       INT,
            roster_id_2       INT,
            winner_roster_id  INT,
            loser_roster_id   INT,
            placement         INT,
            PRIMARY KEY (league_id, bracket, round, match_id)
        )
    """)


def roster_ref(v):
    """A bracket slot is a roster_id (int) or a {'w'/'l': match} reference dict."""
    return v if isinstance(v, int) else None


def load_bracket(cur, league, bracket):
    lid = league["league_id"]
    season = int(league.get("season") or 0)
    endpoint = "winners_bracket" if bracket == "winners" else "losers_bracket"
    data = get(f"league/{lid}/{endpoint}") or []

    rows = []
    seen = set()
    for g in data:
        rnd = g.get("r")
        mid = g.get("m")
        key = (rnd, mid)
        if key in seen:
            continue
        seen.add(key)
        rows.append((
            lid, season, bracket, rnd, mid,
            roster_ref(g.get("t1")), roster_ref(g.get("t2")),
            g.get("w"), g.get("l"), g.get("p"),
        ))

    if rows:
        execute_values(cur, """
            INSERT INTO playoff_matchups
                (league_id, season, bracket, round, match_id,
                 roster_id_1, roster_id_2, winner_roster_id, loser_roster_id, placement)
            VALUES %s
            ON CONFLICT (league_id, bracket, round, match_id) DO UPDATE SET
                season           = EXCLUDED.season,
                roster_id_1      = EXCLUDED.roster_id_1,
                roster_id_2      = EXCLUDED.roster_id_2,
                winner_roster_id = EXCLUDED.winner_roster_id,
                loser_roster_id  = EXCLUDED.loser_roster_id,
                placement        = EXCLUDED.placement
        """, rows, page_size=500)
    return len(rows)


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
            w = load_bracket(cur, league, "winners")
            l = load_bracket(cur, league, "losers")
            season = int(league.get("season") or 0)
            print(f"    season {season}: winners {w}, losers {l}")
            grand_total += w + l
        conn.commit()
        print(f"[{group}] committed.")

    cur.close()
    conn.close()
    print(f"Done. {grand_total} playoff matchup rows total.")


if __name__ == "__main__":
    main()
