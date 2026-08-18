#!/usr/bin/env python3
"""
Phase 4 — Transactions.

For each season of both leagues, loop weeks 1..18 and pull
/league/{id}/transactions/{week}. Each transaction becomes one `transactions`
header row plus one `transaction_moves` child row per player added or dropped.

- adds / drops are dicts {player_id: roster_id}; both can be null.
- FAAB: settings.waiver_bid is the bid for the whole waiver claim -- attached to
  the add move(s), left null on drops and on non-waiver moves.
- created is epoch milliseconds -> stored as TIMESTAMPTZ.

    python pull_transactions.py
"""

from datetime import datetime, timezone

from psycopg2.extras import execute_values

from sleeper_common import get, walk_chain, get_conn, LEAGUES, MAX_WEEK


def ensure_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY,
            league_id      TEXT NOT NULL REFERENCES seasons(league_id),
            season         INT,
            week           INT,
            type           TEXT,
            status         TEXT,
            created        TIMESTAMPTZ
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_moves (
            transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
            player_id      TEXT,
            action         TEXT,
            roster_id      INT,
            faab_bid       INT,
            PRIMARY KEY (transaction_id, player_id, action)
        )
    """)


def epoch_ms_to_ts(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def load_season(cur, league):
    lid = league["league_id"]
    season = int(league.get("season") or 0)

    header_rows = []
    move_rows = []
    move_seen = set()  # (transaction_id, player_id, action) dedupe within pull

    for week in range(1, MAX_WEEK + 1):
        txns = get(f"league/{lid}/transactions/{week}")
        if not txns:
            continue
        for t in txns:
            tid = t.get("transaction_id")
            if not tid:
                continue
            header_rows.append((
                tid, lid, season, week,
                t.get("type"), t.get("status"),
                epoch_ms_to_ts(t.get("created")),
            ))
            settings = t.get("settings") or {}
            faab = settings.get("waiver_bid")
            for action, key in (("add", "adds"), ("drop", "drops")):
                moves = t.get(key) or {}
                for pid, rid in moves.items():
                    dedupe = (tid, pid, action)
                    if dedupe in move_seen:
                        continue
                    move_seen.add(dedupe)
                    move_rows.append((
                        tid, pid, action, rid,
                        faab if action == "add" else None,
                    ))

    if header_rows:
        execute_values(cur, """
            INSERT INTO transactions
                (transaction_id, league_id, season, week, type, status, created)
            VALUES %s
            ON CONFLICT (transaction_id) DO UPDATE SET
                league_id = EXCLUDED.league_id,
                season    = EXCLUDED.season,
                week      = EXCLUDED.week,
                type      = EXCLUDED.type,
                status    = EXCLUDED.status,
                created   = EXCLUDED.created
        """, header_rows, page_size=1000)
    if move_rows:
        execute_values(cur, """
            INSERT INTO transaction_moves
                (transaction_id, player_id, action, roster_id, faab_bid)
            VALUES %s
            ON CONFLICT (transaction_id, player_id, action) DO UPDATE SET
                roster_id = EXCLUDED.roster_id,
                faab_bid  = EXCLUDED.faab_bid
        """, move_rows, page_size=1000)

    print(f"    season {season} ({lid}): {len(header_rows)} txns, {len(move_rows)} moves")
    return len(header_rows), len(move_rows)


def main():
    conn = get_conn()
    cur = conn.cursor()
    ensure_schema(cur)
    conn.commit()

    tot_t = tot_m = 0
    for group, start_id in LEAGUES.items():
        chain = walk_chain(start_id)
        if not chain:
            print(f"[{group}] no league found for id {start_id}")
            continue
        seasons = ", ".join(l.get("season", "?") for l in chain)
        print(f"[{group}] {len(chain)} season(s): {seasons}")
        for league in chain:
            t, m = load_season(cur, league)
            tot_t += t
            tot_m += m
        conn.commit()
        print(f"[{group}] committed.")

    cur.close()
    conn.close()
    print(f"Done. {tot_t} transactions, {tot_m} moves total.")


if __name__ == "__main__":
    main()
