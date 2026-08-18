#!/usr/bin/env python3
"""
Phase 3 — Drafts.

For each season of both leagues, fetch the league's drafts (usually one), then
each draft's picks, and write them to `draft_picks`. Snake and auction leagues
are both handled: the auction dollar amount lives in each pick's
`metadata.amount` and is stored in the `amount` column (null for snake drafts).

    python pull_drafts.py
"""

from psycopg2.extras import execute_values

from sleeper_common import get, walk_chain, get_conn, LEAGUES


def ensure_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS draft_picks (
            draft_id    TEXT NOT NULL,
            league_id   TEXT NOT NULL REFERENCES seasons(league_id),
            season      INT  NOT NULL,
            round       INT,
            pick_no     INT,
            draft_slot  INT,
            roster_id   INT,
            user_id     TEXT REFERENCES managers(user_id),   -- picked_by
            player_id   TEXT,
            is_keeper   BOOLEAN,
            amount      INT,                                   -- auction $ (nullable)
            PRIMARY KEY (draft_id, pick_no)
        )
    """)


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def load_season(cur, league):
    lid = league["league_id"]
    season = int(league.get("season") or 0)

    drafts = get(f"league/{lid}/drafts") or []
    season_rows = 0
    for d in drafts:
        draft_id = d.get("draft_id")
        if not draft_id:
            continue
        picks = get(f"draft/{draft_id}/picks") or []
        rows = []
        seen = set()  # guard against a duplicate pick_no within one draft
        for p in picks:
            pick_no = p.get("pick_no")
            if pick_no is None or pick_no in seen:
                continue
            seen.add(pick_no)
            meta = p.get("metadata") or {}
            rows.append((
                draft_id, lid, season,
                p.get("round"), pick_no, p.get("draft_slot"),
                p.get("roster_id"), p.get("picked_by"), p.get("player_id"),
                bool(p.get("is_keeper")) if p.get("is_keeper") is not None else None,
                to_int(meta.get("amount")),
            ))
        if rows:
            execute_values(cur, """
                INSERT INTO draft_picks
                    (draft_id, league_id, season, round, pick_no, draft_slot,
                     roster_id, user_id, player_id, is_keeper, amount)
                VALUES %s
                ON CONFLICT (draft_id, pick_no) DO UPDATE SET
                    league_id  = EXCLUDED.league_id,
                    season     = EXCLUDED.season,
                    round      = EXCLUDED.round,
                    draft_slot = EXCLUDED.draft_slot,
                    roster_id  = EXCLUDED.roster_id,
                    user_id    = EXCLUDED.user_id,
                    player_id  = EXCLUDED.player_id,
                    is_keeper  = EXCLUDED.is_keeper,
                    amount     = EXCLUDED.amount
            """, rows, page_size=1000)
            season_rows += len(rows)
        print(f"    season {season} draft {draft_id} ({d.get('type')}): {len(rows)} picks")
    if not drafts:
        print(f"    season {season} ({lid}): no drafts")
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
    print(f"Done. {grand_total} draft picks total.")


if __name__ == "__main__":
    main()
