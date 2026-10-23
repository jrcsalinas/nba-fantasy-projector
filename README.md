# NBA Fantasy Projector

Projections, draft rankings, and lineup advice for NBA Fantasy.

## What it does

A FastAPI backend and web page that help fantasy basketball players make decisions. Today it
ships a **trending players dashboard**: a 9-category ranking (PTS, REB, AST, STL, BLK, 3PM, FG%,
FT%, TO) over the season, the last 15 days, or the last 30 days, with each player's rank change
since the previous snapshot. A second tab, **My roster** (`/roster`), shows only your players, a
team row with your weakest categories, and waiver suggestions for them. Rankings are statistical
estimates, not guarantees.

## Quick start

```powershell
uv sync
uv run python -m nba_projector.jobs.refresh --no-wait   # load data once
uv run uvicorn nba_projector.main:app --reload
```

Then open <http://127.0.0.1:8000/>. The API docs are at <http://127.0.0.1:8000/docs>.

For the My roster tab, copy `roster.example.json` to `data/roster.json` and list your players
(see [My roster](#my-roster)).

The dashboard shows per-game stats (GP, MPG, the nine categories, FGA, FTA) shaded by z-score;
the **Z-score** toggle shows the z-scores instead. Season stats include last season's averages
until a player has 20 games. Snapshots saved before per-game stats were stored show "–" for stats
until you re-run the job for that date (`--date YYYY-MM-DD --no-wait`).

If the date has no games the job writes nothing; pass `--date YYYY-MM-DD` with a past game date.
Last 15 and Last 30 stay empty early in the season until players reach the minimum games.

## Configuration

Copy `.env.example` to `.env` to override the defaults.

| Variable | Default | Purpose |
| --- | --- | --- |
| `NBA_PROJECTOR_DB_PATH` | `data/projector.db` | SQLite database file |
| `NBA_PROJECTOR_RAW_DIR` | `data/raw` | Raw `nba_api` responses, kept apart from cleaned data |
| `NBA_PROJECTOR_ROSTER_PATH` | `data/roster.json` | Your roster file for the My roster tab |

## Usage

```powershell
curl "http://127.0.0.1:8000/api/v1/rankings?window=l15&sort=pts&limit=10"
```

- `window`: `season` (default), `l15`, `l30`
- `sort`: `overall` (default), `pts`, `reb`, `ast`, `stl`, `blk`, `fg3m`, `fg_pct`, `ft_pct`, `tov`
- `limit`: 1 to 500, default 100

Each row has `z` (category z-scores) and `stats` (per-game averages, or `null` for old snapshots).

The endpoint reads saved snapshots only and never calls `nba_api`. It returns 404 until the first
refresh has run.

## My roster

Open <http://127.0.0.1:8000/roster>. Put your players in `data/roster.json` (the `data/` folder is
gitignored, so the roster stays local). Players are matched by `player_id`; `name` is only for
readability, and `il` is optional:

```json
{
  "active": [{"player_id": 1630178, "name": "Tyrese Maxey"}],
  "il": [{"player_id": 1629632, "name": "Coby White"}]
}
```

Find a `player_id` in the rankings API (`/api/v1/rankings`) or on a player's page at nba.com. The
file is re-read on every request, so edits show up on reload.

- The team row sums the z-scores of your active players; IL players are left out. Outlined cells
  are your three weakest categories.
- A player below the games or minutes threshold in a window shows as "Not ranked in this window".
- Waiver suggestions list the best players per weak category who are not on your roster and rank
  below a cutoff overall (default 150, adjustable on the page). That is an **estimate** of who is
  available, not your league's actual free agents.

```powershell
curl "http://127.0.0.1:8000/api/v1/roster?window=season&sort=overall"
curl "http://127.0.0.1:8000/api/v1/roster/waivers?cutoff=150&limit=5"
```

## Daily refresh

`jobs.refresh` is its own entry point, run by an external scheduler. Schedule it once a day
around late morning US Eastern time. It finds the day's last game, waits until about three hours
after it starts, polls until every game is final (giving up six hours after the start), then
saves one snapshot per window for that game date. Re-running a date replaces its snapshot, and a
failed fetch leaves the previous snapshot untouched.

On Windows, use Task Scheduler with the action:

```powershell
uv run --project C:\path\to\nba-fantasy-projector python -m nba_projector.jobs.refresh
```

On Linux or macOS, use cron:

```
0 11 * * *  cd /path/to/nba-fantasy-projector && uv run python -m nba_projector.jobs.refresh
```

Use the machine's US Eastern time, or adjust the hour.

## Project structure

```
src/nba_projector/
  main.py      # FastAPI app; serves the pages and static files
  config.py    # settings from environment variables
  api/         # rankings and roster routes, schemas, row building
  adapters/    # DataProvider protocol, nba_api provider, SQLite repository, roster file
  domain/      # pure models, window averaging, 9-cat ranking math, roster math
  jobs/        # daily refresh job
  web/         # dashboard and roster pages: HTML, JS, CSS
tests/         # mirrors src/; fixtures in tests/fixtures/
```

## Development

```powershell
uv run pytest
uv run ruff check
uv run ruff format --check
```

Tests never call `nba_api`; fixtures in `tests/fixtures/` are trimmed recorded responses.

## Contributing

Branch as `<type>/<short-description>` and use Conventional Commits. Make sure tests and lint
pass before opening a PR.

## License

TBD.
