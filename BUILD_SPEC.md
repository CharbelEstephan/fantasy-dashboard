# Fantasy League History — Data Pipeline Build Spec

Hand this file to Claude Code as the blueprint. Read it fully before writing code.
The goal: pull the **complete** Sleeper dataset for two leagues into an existing
Neon Postgres database, on top of a foundation that's already built, so a Grafana
dashboard can be built on it afterward. Everything must be **idempotent** (safe to
re-run weekly).

---

## 1. Context & current state (DO NOT rebuild these)

This is a working project, not a greenfield. The following already exist in the
Neon database and must **not** be dropped or recreated:

- **Tables already populated:** `managers`, `seasons`, `season_teams`, `team_week`
- **View already built:** `v_matchup_results` (head-to-head feed with names + margins)
- **Existing script:** `pull_sleeper.py` — pulls league metadata, users, rosters,
  and weekly team scores by walking each league's `previous_league_id` chain.
- **Naming layer:** `managers` has an `alias` column with custom display names.
  Always display managers as `COALESCE(alias, display_name)`.

Extend this project — add new tables and new pull logic alongside what's there.
The only permitted change to existing objects is **rebuilding `v_matchup_results`**
if a new column is needed, and that must be done with `DROP VIEW` + `CREATE VIEW`
(NOT `CREATE OR REPLACE` — Postgres rejects column reorders on replace; we hit this).

## 2. Environment & conventions

- **Runtime:** Python 3.10 on Windows (PowerShell). Packages installed to system
  Python (no active venv). Use `psycopg2` (`psycopg2-binary`) and `requests`.
- **DB connection:** read from `DATABASE_URL` env var. **Never** hardcode it or commit
  it. Add a `.gitignore` that excludes `.env`, `venv/`, `__pycache__/`, and any
  cached data files.
- **Target DB:** Neon project "fantasy football" (separate from the user's TCG project).
- **Repo:** this becomes its own git repo `fantasy-dashboard` (like the user's
  `tcg-tracker` repo). Include a `requirements.txt` and a short `README.md`.
- **Batch inserts:** use `psycopg2.extras.execute_values` for the large tables.
- **All writes are upserts:** `INSERT ... ON CONFLICT (...) DO UPDATE`. Re-running any
  script must never duplicate rows — it refreshes the current season and fills new weeks.

## 3. The two leagues

| Label (league_group) | Newest league_id      | Seasons available     |
|----------------------|-----------------------|-----------------------|
| Sundays For the Boys | `1389330278945660928` | 2023, 2024, 2025, 2026 |
| Sportz Ball Boys     | `1312195283332911104` | 2025, 2026            |

The league *names* come from the Sleeper API; `league_group` is already stored on
`seasons`. Both leagues are 12 teams. 2026 has just started — expect thin/empty
current-season data until games are played. Walk `previous_league_id` from each
newest id to reach every prior season (already how `pull_sleeper.py` works).

## 4. Sleeper API reference (read-only, no auth, base `https://api.sleeper.app/v1`)

Verified field shapes to rely on:

- **`GET /league/{id}`** → `previous_league_id`, `season`, `name`, `total_rosters`,
  `settings.playoff_week_start`, `settings.playoff_teams`, `status`.
- **`GET /league/{id}/users`** → `user_id`, `display_name`, `metadata.team_name`, `avatar`.
- **`GET /league/{id}/rosters`** → `roster_id`, `owner_id`, `players`, `starters`,
  and `settings.{wins,losses,ties,fpts,fpts_decimal,fpts_against,fpts_against_decimal,
  ppts,ppts_decimal,waiver_position,total_moves,division}`. **Points are split**: real
  value = `fpts + fpts_decimal/100`. Same for `ppts` (potential/optimal points).
- **`GET /league/{id}/matchups/{week}`** → per team: `roster_id`, `matchup_id`,
  `points` (team total, float), `starters` (ordered player_id list), `players` (all
  player_ids incl. bench), `players_points` (dict `{player_id: points}`),
  `starters_points` (ordered, parallel to `starters`). **Two entries with the same
  `matchup_id` are opponents.** Bench = `players` minus `starters`. `matchup_id` can
  be null in bye/consolation weeks; `players` can be null for empty slots — guard both.
- **`GET /league/{id}/drafts`** → array of drafts (usually one); each has `draft_id`,
  `type` (snake/auction), `season`, `settings`, `draft_order`.
- **`GET /draft/{draft_id}/picks`** → `player_id`, `picked_by` (user_id), `roster_id`,
  `round`, `draft_slot`, `pick_no`, `is_keeper`, `metadata` (player name/pos/team; and
  `amount` for auction leagues).
- **`GET /league/{id}/transactions/{week}`** → `transaction_id`, `type`
  (trade/waiver/free_agent), `status`, `roster_ids`, `adds` (`{player_id: roster_id}`),
  `drops` (`{player_id: roster_id}`), `draft_picks`, `settings.waiver_bid` (FAAB),
  `created` (epoch ms), `consenter_ids`. Loop weeks 1..18.
- **`GET /league/{id}/winners_bracket`** and **`/losers_bracket`** → each row: `r`
  (round), `m` (match id), `t1`, `t2` (roster_ids), `w` (winner roster_id),
  `l` (loser roster_id), `p` (placement). Authoritative playoff structure.
- **`GET /league/{id}/traded_picks`** → traded draft picks (only relevant if keeper/dynasty).
- **`GET /players/nfl`** → ONE big (~5 MB) object keyed by `player_id`, each with
  `full_name`, `position`, `team`, `fantasy_positions`, `status`. **Call at most once
  per day** — cache to a local JSON file and reuse. Note: team defenses are keyed by
  team abbreviation (e.g. `"PHI"`) not a numeric id, and have no `full_name` — handle
  non-numeric player_ids gracefully.
- **`GET /state/nfl`** → `week`, `season`, `leg`. Use this so the weekly run knows the
  current week automatically instead of hardcoding.

Rate limit: stay well under 1000 requests/minute. Add a small `time.sleep` between
calls and batch by week. Our total volume is low, but be polite.

## 5. Target schema (new tables to add)

```sql
-- NFL player reference (from /players/nfl, cached locally, refreshed weekly)
CREATE TABLE IF NOT EXISTS players (
    player_id  TEXT PRIMARY KEY,        -- numeric id, or team abbrev for defenses
    full_name  TEXT,
    position   TEXT,
    team       TEXT
);

-- Every draft pick, every season
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
);

-- Player-level weekly detail: starters + bench, per player, per team, per week.
-- This is the biggest table (~17k rows) and powers bench-management stats.
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
);

-- Transactions header + a child table for the player moves inside each one
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    league_id      TEXT NOT NULL REFERENCES seasons(league_id),
    season         INT,
    week           INT,
    type           TEXT,                 -- trade / waiver / free_agent
    status         TEXT,
    created        TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS transaction_moves (
    transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
    player_id      TEXT,
    action         TEXT,                 -- add / drop
    roster_id      INT,
    faab_bid       INT,                  -- nullable
    PRIMARY KEY (transaction_id, player_id, action)
);

-- Playoff bracket games (winners + losers brackets)
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
);
```

Also **extend `season_teams`** with optimal-points + activity columns (use
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`):
`potential_points NUMERIC(8,2)` (from `ppts`), `total_moves INT`,
`waiver_position INT`, `division INT`.

## 6. Build order (phased — verify each before moving on)

- **Phase 0 — Repo setup.** Init `fantasy-dashboard` git repo, add `.gitignore`,
  `requirements.txt` (`requests`, `psycopg2-binary`), move existing `pull_sleeper.py`
  in, write a short `README.md`. Confirm `DATABASE_URL` reads from env.
- **Phase 1 — Players reference.** `pull_players.py`: download `/players/nfl` to a
  local cache file (skip re-download if cached copy is <24h old), load into `players`.
  Needed before draft/roster data is human-readable.
- **Phase 2 — Player-week (bench data).** `pull_player_weeks.py`: for each season/week,
  read matchups, and for every team write one `player_week` row per player in
  `players`, with `points` from `players_points` and `is_starter = player_id in starters`.
  This is the big one — use `execute_values`.
- **Phase 3 — Drafts.** `pull_drafts.py`: for each season, get drafts → picks →
  `draft_picks`.
- **Phase 4 — Transactions.** `pull_transactions.py`: loop weeks, write `transactions`
  + `transaction_moves` (expand `adds`/`drops`/FAAB).
- **Phase 5 — Playoff brackets.** `pull_brackets.py`: winners + losers bracket →
  `playoff_matchups`.
- **Phase 6 — Extend season_teams.** Backfill `potential_points` etc. from roster
  settings (in the existing rosters pull or a small migration).
- **Phase 7 — Orchestrator.** `pull_all.py` runs every phase in order for both
  leagues. Uses `/state/nfl` to know the current week. This is the single command the
  user runs every Tuesday.
- **Phase 8 — Analytics views.** Build read-only views for Grafana (see §7).

## 7. Analytics views to build (Phase 8)

Build these as SQL views so Grafana queries stay simple. Use
`COALESCE(alias, display_name)` for all manager names, and always expose
`league_group` so the dashboard can filter per league.

- **`v_manager_season`** — per manager per season: W-L-T, points for/against,
  potential points, coaching efficiency (`points_for / potential_points`), finish.
- **`v_alltime`** — all-time per manager: total record, win %, avg score, titles.
- **`v_h2h`** — head-to-head matrix: for each (manager, opponent) pair, all-time
  W-L and avg margin. Powers the "who owns who" grid.
- **`v_bench`** — per team-week and per season: bench points (sum of `player_week`
  where `not is_starter`), and points-left-on-bench vs optimal. The bench-management
  leaderboard.
- **`v_luck`** — all-play record (how each team would do vs the whole league each
  week) vs actual record → luck index.
- **`v_draft_tendencies`** — from `draft_picks` + `players`: repeat picks (same player
  drafted by same manager across seasons), positional tendencies by round.
- **`v_transactions_summary`** — activity per manager: trades, waiver claims, FAAB spent.

## 8. Gotchas (things that will bite you)

- **Split points fields:** `fpts`/`fpts_decimal` and `ppts`/`ppts_decimal` combine as
  `whole + decimal/100`. Matchup `points` is already a clean float.
- **View changes:** `DROP VIEW` + `CREATE VIEW`, never `CREATE OR REPLACE` when column
  order/names change.
- **Null guards:** `matchup_id`, `players`, and `players_points` can be null in bye or
  empty weeks — skip cleanly.
- **Defenses:** player_ids for team defenses are team abbreviations, not numbers, and
  lack `full_name`. Don't crash on them.
- **Players file size:** ~5 MB; call `/players/nfl` at most once/day; cache locally and
  gitignore the cache.
- **Identity keys:** `roster_id` is per-season; `owner_id`/`user_id` is the stable
  identity. Always carry `user_id` into every fact table so cross-season joins work.
- **2026 is empty right now:** don't treat missing current-season rows as a bug.
- **Secrets:** `DATABASE_URL` stays in the environment; nothing sensitive in git.

## 9. Verification queries (run after the relevant phase)

```sql
-- Phase 1: players loaded
select count(*) from players;

-- Phase 2: player-week volume looks right (~ teams * players * weeks * seasons)
select season, count(*) from player_week group by season order by season;

-- Phase 2 sanity: a team's bench points for one week
select roster_id, sum(points) filter (where not is_starter) as bench_pts
from player_week where league_id = '1261015424884023296' and week = 5
group by roster_id order by bench_pts desc;

-- Phase 3: picks per draft
select draft_id, count(*) from draft_picks group by draft_id;

-- Phase 4: transactions by type
select type, count(*) from transactions group by type;

-- Phase 5: bracket rows exist per completed season
select season, bracket, count(*) from playoff_matchups group by season, bracket;
```

---

**Definition of done:** `python pull_all.py` runs clean end to end for both leagues,
every table populated for completed seasons, all Phase 8 views return sensible rows,
and re-running changes nothing except refreshing the live season. Then it's ready for
Grafana.
