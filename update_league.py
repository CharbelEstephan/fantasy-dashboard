#!/usr/bin/env python3
"""
Phase 7 — Orchestrator.

Runs every pull phase in order for both leagues. This is the single command to
run every Tuesday during the season; everything downstream is idempotent, so a
weekly run just refreshes the current season and fills newly-played weeks.

Order:
    0. pull_sleeper          seasons, managers, season_teams, team_week
    1. pull_players          NFL player reference (cached <24h)
    2. pull_player_weeks     player-level weekly detail (starters + bench)
    3. pull_drafts           draft picks
    4. pull_transactions     trades / waivers / FAAB
    5. pull_brackets         playoff brackets
    6. pull_season_teams_extra   optimal points + activity columns

/state/nfl is queried up front so the run reports the current NFL week/season.

    python update_league.py
"""

import sys
import time
import traceback

import load_env  # noqa: F401  -- loads .env into os.environ on import
from sleeper_common import current_state

import pull_sleeper
import pull_players
import pull_player_weeks
import pull_drafts
import pull_transactions
import pull_brackets
import pull_season_teams_extra
import build_views

PHASES = [
    ("Phase 0  base (leagues/users/rosters/scores)", pull_sleeper.main),
    ("Phase 1  players reference",                   pull_players.main),
    ("Phase 2  player-week (bench data)",            pull_player_weeks.main),
    ("Phase 3  drafts",                              pull_drafts.main),
    ("Phase 4  transactions",                        pull_transactions.main),
    ("Phase 5  playoff brackets",                    pull_brackets.main),
    ("Phase 6  season_teams extra columns",          pull_season_teams_extra.main),
    ("Phase 8  analytics views",                     build_views.main),
]


def main():
    state = current_state()
    season = state.get("season", "?")
    week = state.get("week", "?")
    print("=" * 64)
    print(f"Sleeper pull - current NFL state: season {season}, week {week}")
    print("=" * 64)

    started = time.time()
    failures = []
    for label, fn in PHASES:
        print(f"\n### {label} ###")
        t0 = time.time()
        try:
            fn()
            print(f"--- ok ({time.time() - t0:.1f}s)")
        except Exception:
            failures.append(label)
            print(f"!!! FAILED: {label}")
            traceback.print_exc()

    elapsed = time.time() - started
    print("\n" + "=" * 64)
    if failures:
        print(f"Completed with {len(failures)} failed phase(s) in {elapsed:.1f}s:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"All phases completed cleanly in {elapsed:.1f}s.")


if __name__ == "__main__":
    main()
