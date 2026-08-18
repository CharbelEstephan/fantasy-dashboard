# fantasy-dashboard

Data pipeline that pulls the complete [Sleeper](https://sleeper.com) dataset for two
fantasy football leagues into a Neon Postgres database, so a Grafana dashboard can be
built on top of it. Everything is **idempotent** — safe to re-run every week.

## Leagues

| league_group          | Newest league_id      | Seasons                |
|-----------------------|-----------------------|------------------------|
| Sundays For the Boys  | `1389330278945660928` | 2023, 2024, 2025, 2026 |
| Sportz Ball Boys      | `1312195283332911104` | 2025, 2026             |

Each script walks the `previous_league_id` chain back from the newest id to reach every
prior season.

## Setup

```bash
pip install -r requirements.txt
```

Set your Neon connection string in the environment (never commit it):

```powershell
# PowerShell (current session)
$env:DATABASE_URL = "postgresql://...your-neon-connection-string..."

# Or persist it for your user account:
[System.Environment]::SetEnvironmentVariable('DATABASE_URL', 'postgresql://...', 'User')
```

## Usage

Every Tuesday, run the orchestrator (once it exists — Phase 7):

```bash
python pull_all.py
```

Or run an individual phase:

| Phase | Script                  | What it loads                                   |
|-------|-------------------------|-------------------------------------------------|
| 0     | (repo setup)            | git repo, gitignore, requirements               |
| —     | `pull_sleeper.py`       | leagues, users, rosters, weekly team scores     |
| 1     | `pull_players.py`       | NFL player reference (`players`)                |
| 2     | `pull_player_weeks.py`  | player-level weekly detail (`player_week`)      |
| 3     | `pull_drafts.py`        | draft picks (`draft_picks`)                     |
| 4     | `pull_transactions.py`  | trades / waivers / FAAB (`transactions`)        |
| 5     | `pull_brackets.py`      | playoff brackets (`playoff_matchups`)           |
| 6     | (season_teams backfill) | optimal points + activity columns               |
| 7     | `pull_all.py`           | runs every phase for both leagues               |
| 8     | (SQL views)             | analytics views for Grafana                     |

## Conventions

- All writes are upserts (`INSERT ... ON CONFLICT ... DO UPDATE`) — re-running never
  duplicates rows.
- Manager display names: always `COALESCE(alias, display_name)`.
- Sleeper points are split fields: real value = `fpts + fpts_decimal / 100`.
- `roster_id` is per-season; `user_id` is the stable cross-season identity.
- The `/players/nfl` dump (~5 MB) is cached locally and refreshed at most once/day.

## Target

Neon project **"fantasy football"** — reads `DATABASE_URL` from the environment.
