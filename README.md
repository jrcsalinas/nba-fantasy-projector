# NBA Fantasy Projector

Projections, draft rankings, and lineup advice for NBA Fantasy.

## What it does

A FastAPI backend and web page that help fantasy basketball players make decisions. Today it
ships a **trending players dashboard**: a 9-category ranking (PTS, REB, AST, STL, BLK, 3PM, FG%,
FT%, TO) over the season, the last 15 days, or the last 30 days, with each player's rank change
since the previous snapshot. Rankings are statistical estimates, not guarantees.

## Quick start

```powershell
uv sync
uv run python -m nba_projector.jobs.refresh --no-wait   # load data once
uv run uvicorn nba_projector.main:app --reload
```

Then open <http://127.0.0.1:8000/>. The API docs are at <http://127.0.0.1:8000/docs>.

If the date has no games the job writes nothing; pass `--date YYYY-MM-DD` with a past game date.
Last 15 and Last 30 stay empty early in the season until players reach the minimum games.

## Configuration

Copy `.env.example` to `.env` to override the defaults.

| Variable | Default | Purpose |
| --- | --- | --- |
| `NBA_PROJECTOR_DB_PATH` | `data/projector.db` | SQLite database file |
| `NBA_PROJECTOR_RAW_DIR` | `data/raw` | Raw `nba_api` responses, kept apart from cleaned data |

## Usage

```powershell
curl "http://127.0.0.1:8000/api/v1/rankings?window=l15&sort=pts&limit=10"
```

- `window`: `season` (default), `l15`, `l30`
- `sort`: `overall` (default), `pts`, `reb`, `ast`, `stl`, `blk`, `fg3m`, `fg_pct`, `ft_pct`, `tov`
- `limit`: 1 to 500, default 100

The endpoint reads saved snapshots only and never calls `nba_api`. It returns 404 until the first
refresh has run.

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
  main.py      # FastAPI app; serves the page and static files
  config.py    # settings from environment variables
  api/         # rankings route, schemas, and row building
  adapters/    # DataProvider protocol, nba_api provider, SQLite repository
  domain/      # pure models, window averaging, and 9-cat ranking math
  jobs/        # daily refresh job
  web/         # index.html, app.js, style.css
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
