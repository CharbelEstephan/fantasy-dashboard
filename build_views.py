#!/usr/bin/env python3
"""
Phase 8 - Build the analytics views.

Executes views.sql against the database. Each view is rebuilt with
DROP VIEW + CREATE VIEW (idempotent), so this is safe to re-run any time and is
also invoked at the end of update_league.py if you wire it in.

    python build_views.py
"""

import os

from sleeper_common import get_conn

SQL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views.sql")

VIEWS = [
    "v_manager_season", "v_alltime", "v_h2h", "v_bench",
    "v_luck", "v_draft_tendencies", "v_transactions_summary",
]


def main():
    with open(SQL_FILE, "r", encoding="utf-8") as f:
        sql = f.read()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()

    for v in VIEWS:
        cur.execute(f"SELECT count(*) FROM {v}")
        print(f"  {v:26} {cur.fetchone()[0]:>6} rows")

    cur.close()
    conn.close()
    print("Views built.")


if __name__ == "__main__":
    main()
