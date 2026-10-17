# Trending Players Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A home page that ranks NBA players in 9-cat (Rank, Trend vs. previous snapshot, nine category z-scores, Total), for Season / Last 15 / Last 30 days, refreshed daily after the last game of the day.

**Architecture:** A scheduled job (own entry point) fetches game logs through a `DataProvider` adapter, stores them in SQLite, computes ranking snapshots with pure domain code, and saves one snapshot per window per game date. FastAPI reads saved snapshots only and serves a JSON endpoint plus a plain HTML/JS page.

**Tech Stack:** Python 3.12+, FastAPI, pydantic-settings, nba_api, SQLite (stdlib `sqlite3`), uv, ruff, pytest.

**Spec:** `docs/superpowers/specs/2026-10-16-trending-dashboard-design.md`

## Global Constraints

- Python 3.12+, managed with uv; `ruff check` and `ruff format --check` must pass; line length 100.
- Full type hints, Google-style docstrings on public functions and classes, `logging` not `print`.
- Add only folders that have contents; follow the existing layout in `src/nba_projector/`.
- 9-cat only: PTS, REB, AST, STL, BLK, 3PM, FG%, FT%, TO. TO is reversed (fewer is better).
- Windows: Season (default), Last 15 days, Last 30 days.
- Domain and ranking code is pure: no network or database calls.
- Request handlers never call `nba_api`. A failed refresh never overwrites the last good snapshot.
- `nba_api` calls: timeout, retries with backoff, throttling; raw responses kept apart from cleaned data.
- Snapshot date is the game date in US Eastern time; one snapshot per window per date; re-runs replace.
- The page shows the note "Rankings are statistical estimates, not guarantees." with no betting framing.
- No live `nba_api` calls in tests.

## Review Focus

- A player with zero FGA or FTA in a window: FG%/FT% impact is 0.0, no crash or NaN.
- A window with no eligible players (season opener, All-Star break): empty ranking, API returns `rows: []` with 200.
- A category where every player has the same value (std = 0): z-score is 0.0, not NaN.
- A traded player appears under two teams in the logs: one row per `player_id`.
- Job re-run for the same date does not duplicate; a failed fetch leaves the previous snapshot intact; a no-games day writes nothing.

## File Structure

- `src/nba_projector/config.py`: `Settings` and `get_settings()`.
- `src/nba_projector/domain/models.py`: enums and dataclasses shared by all layers.
- `src/nba_projector/domain/windows.py`: `season_for`, `average_logs`, `blend_with_prior`.
- `src/nba_projector/domain/ranking.py`: `rank_players`, `compute_trends`.
- `src/nba_projector/adapters/provider.py`: `DataProvider` protocol, `ScheduledGame`.
- `src/nba_projector/adapters/nba_api_provider.py`: `NbaApiProvider`, parsers, retry helper.
- `src/nba_projector/adapters/repository.py`: SQLite `Repository`.
- `src/nba_projector/jobs/refresh.py`: wait logic, `run_refresh`, CLI.
- `src/nba_projector/api/routes.py`: add `GET /api/v1/rankings`; `src/nba_projector/api/schemas.py`: response models.
- `src/nba_projector/web/{index.html,app.js,style.css}`: the page.
- `tests/` mirrors `src/`; fixtures in `tests/fixtures/`.

---

### Task 1: Domain models, windows, and ranking math

**Files:**
- Create: `src/nba_projector/domain/__init__.py`, `models.py`, `windows.py`, `ranking.py`
- Modify: `pyproject.toml` (add dev dependency `pytest`; add `[tool.pytest.ini_options] testpaths = ["tests"]`), `.gitignore` (add `data/`)
- Test: `tests/domain/test_windows.py`, `tests/domain/test_ranking.py` (plus `tests/__init__.py`, `tests/domain/__init__.py`)

**Interfaces:**
- Produces, in `models.py`:
  - `class Category(StrEnum)`: `PTS="pts"`, `REB="reb"`, `AST="ast"`, `STL="stl"`, `BLK="blk"`, `FG3M="fg3m"`, `FG_PCT="fg_pct"`, `FT_PCT="ft_pct"`, `TOV="tov"`.
  - `class Window(StrEnum)`: `SEASON="season"`, `LAST_15="l15"`, `LAST_30="l30"`.
  - `SORT_KEYS: tuple[str, ...]` = `("overall", *[c.value for c in Category])`.
  - `@dataclass(frozen=True) GameLog`: `season: str`, `game_id: str`, `game_date: date`, `player_id: int`, `player_name: str`, `minutes: float`, and ints `pts, reb, ast, stl, blk, fg3m, fgm, fga, ftm, fta, tov`.
  - `@dataclass(frozen=True) PlayerAverages`: `player_id: int`, `player_name: str`, `games: int`, `minutes_pg: float`, and floats `pts, reb, ast, stl, blk, fg3m, fgm, fga, ftm, fta, tov` (all per game).
  - `@dataclass(frozen=True) RankedPlayer`: `player_id: int`, `player_name: str`, `z: dict[Category, float]`, `total: float`, `ranks: dict[str, int]` (keys from `SORT_KEYS`).
- Produces, in `windows.py`:
  - `season_for(day: date) -> str` (Aug 1 onward starts the next season label, e.g. `date(2026,10,16)` gives `"2026-27"`, `date(2027,3,1)` gives `"2026-27"`).
  - `average_logs(logs: Sequence[GameLog], window: Window, as_of: date) -> dict[int, PlayerAverages]`.
  - `blend_with_prior(current: PlayerAverages | None, prior: PlayerAverages | None, full_weight_games: int = 20) -> PlayerAverages | None`.
- Produces, in `ranking.py`:
  - `MIN_GAMES: dict[Window, int] = {SEASON: 10, LAST_15: 5, LAST_30: 10}` and `MIN_MINUTES_PG = 12.0` (builder may tune).
  - `rank_players(averages: Sequence[PlayerAverages], min_games: int, min_minutes_pg: float) -> list[RankedPlayer]`, sorted by overall rank.

- [ ] **Step 1: Commit the existing scaffold and spec before changing anything**

```bash
git add .gitignore .python-version README.md pyproject.toml src uv.lock docs/superpowers/specs
git commit -m "chore: scaffold FastAPI project with uv and ruff"
```
(Two commits if you prefer: scaffold, then `docs: add trending dashboard design`.)

- [ ] **Step 2: Add pytest** with `uv add --dev pytest`, set `testpaths`, add `data/` to `.gitignore`. Run `uv run pytest` and expect "no tests ran".

- [ ] **Step 3: Write failing tests in `tests/domain/test_windows.py`**
  - `test_season_for_october_returns_new_season`: `season_for(date(2026,10,16)) == "2026-27"`.
  - `test_season_for_march_returns_season_started_previous_year`: `season_for(date(2027,3,1)) == "2026-27"`.
  - `test_average_logs_l15_includes_day_minus_14_and_excludes_day_minus_15`: with `as_of=date(2026,11,30)`, a log on 11-16 is counted, a log on 11-15 is not.
  - `test_average_logs_season_ignores_prior_season_logs`.
  - `test_average_logs_traded_player_is_one_row`: two logs, same `player_id`, different `game_id`s, give `games == 2` and one dict entry.
  - `test_blend_with_prior_shrinks_prior_weight_as_games_grow`: current 10 games `pts=20`, prior `pts=10` gives `pts == 15.0` and `games == 20`; current 25 games returns current values unchanged.
  - `test_blend_with_prior_handles_missing_sides`: prior only gives prior values with `games == 20`; current only returns current; both `None` returns `None`.

- [ ] **Step 4: Write failing tests in `tests/domain/test_ranking.py`** (three-player pools built by a small `make_avg(...)` helper; all stats default identical so only the varied stat moves)
  - `test_rank_players_pts_z_scores_use_population_std`: pts 10/20/30 gives z `-1.2247`, `0.0`, `+1.2247` (`pytest.approx(abs=1e-3)`), total equals the pts z, ranks 3/2/1.
  - `test_rank_players_zero_std_category_gives_zero_z`: all other categories z are `0.0`, none NaN.
  - `test_rank_players_tov_is_reversed`: tov 1/2/3 gives z `+1.2247`, `0.0`, `-1.2247`.
  - `test_rank_players_fg_pct_weights_by_attempts`: fgm/fga 8/10, 2/10, 5/10 gives FG z `+1.2247`, `-1.2247`, `0.0` (impact = `fgm - pool_fg_pct * fga`, pool pct = 0.5).
  - `test_rank_players_zero_attempts_gives_zero_impact`: a player with `fga=0` and `fta=0` has finite z for FG% and FT%.
  - `test_rank_players_filters_below_thresholds`: a player under `min_games` or `min_minutes_pg` is absent; empty input returns `[]`.
  - `test_rank_players_category_rank_breaks_ties_by_overall_rank`.

- [ ] **Step 5: Run `uv run pytest`** and expect all of the above to FAIL with import errors.

- [ ] **Step 6: Implement** the signatures above. Approach notes the tests do not fix:
  - `average_logs` windows by calendar days ending on `as_of` inclusive (`as_of - 14 days` to `as_of` for 15 days). Season window uses logs whose `season == season_for(as_of)`.
  - `blend_with_prior` weights per-game values by `current.games` and `max(0, full_weight_games - current.games)`; the returned `games` is the sum of the two weights.
  - `rank_players`: per-category pool mean and population std over eligible players; `RankedPlayer.ranks["overall"]` orders by total descending, `ranks[cat]` by that category's z descending, ties broken by overall rank then `player_id`.

- [ ] **Step 7: Run `uv run pytest` and `uv run ruff check` and `uv run ruff format --check`.** Expect all pass.

- [ ] **Step 8: Commit** `feat(domain): add window averaging, prior blending, and 9-cat ranking`.

---

### Task 2: Trend computation

**Files:**
- Modify: `src/nba_projector/domain/models.py` (add `Trend`), `src/nba_projector/domain/ranking.py`
- Test: `tests/domain/test_trend.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `@dataclass(frozen=True) Trend`: `change: int | None`, `is_new: bool`; `compute_trends(today: Mapping[int, int], previous: Mapping[int, int] | None) -> dict[int, Trend]` where the maps are `player_id -> rank`. Positive `change` means the player moved up.

- [ ] **Step 1: Write failing tests**
  - `test_compute_trends_without_previous_returns_dash_for_all`: every value is `Trend(None, False)`.
  - `test_compute_trends_player_moving_up`: rank 5 to 2 gives `Trend(3, False)`.
  - `test_compute_trends_player_moving_down`: rank 2 to 5 gives `Trend(-3, False)`.
  - `test_compute_trends_unchanged_rank`: `Trend(0, False)`.
  - `test_compute_trends_player_absent_from_previous_is_new`: `Trend(None, True)`.
- [ ] **Step 2: Run** `uv run pytest tests/domain/test_trend.py -v`; expect FAIL.
- [ ] **Step 3: Implement** `Trend` and `compute_trends` as specified.
- [ ] **Step 4: Run** pytest and ruff; expect pass.
- [ ] **Step 5: Commit** `feat(domain): compute rank trend against a previous snapshot`.

---

### Task 3: SQLite repository and settings

**Files:**
- Create: `src/nba_projector/config.py`, `src/nba_projector/adapters/__init__.py`, `adapters/repository.py`, `.env.example`
- Modify: `pyproject.toml` (`uv add pydantic-settings`)
- Test: `tests/adapters/test_repository.py` (use `tmp_path`)

**Interfaces:**
- Consumes: `GameLog`, `RankedPlayer`, `Window`, `Category`, `SORT_KEYS` from Task 1.
- Produces:
  - `Settings(BaseSettings)` with env prefix `NBA_PROJECTOR_`: `db_path: Path = Path("data/projector.db")`, `raw_dir: Path = Path("data/raw")`; `get_settings() -> Settings` (cached).
  - `class Repository`: `__init__(self, db_path: Path)` (creates parent dir and schema, enables WAL);
    - `save_game_logs(self, logs: Sequence[GameLog]) -> None` (upsert on `(player_id, game_id)`);
    - `load_game_logs(self, seasons: Sequence[str]) -> list[GameLog]`;
    - `replace_snapshots(self, snapshot_date: date, snapshots: Mapping[Window, Sequence[RankedPlayer]]) -> None` (one transaction for all windows; deletes that date's rows for each window first);
    - `snapshot_dates(self, window: Window, limit: int = 2) -> list[date]` (newest first);
    - `load_snapshot(self, snapshot_date: date, window: Window) -> list[RankedPlayer]` (ordered by overall rank).
  - Snapshot storage: one wide row per `(snapshot_date, window, player_id)` with `player_name`, `total`, `z_<cat>` and `rank_<key>` columns.

- [ ] **Step 1: Write failing tests**
  - `test_save_game_logs_is_idempotent`: saving the same logs twice loads back the same count.
  - `test_replace_snapshots_replaces_same_date`: write date D twice with different players; only the second set loads.
  - `test_replace_snapshots_rolls_back_on_error`: a snapshot containing an invalid row (builder's choice of failure, e.g. a rank dict missing a key) leaves the previously saved snapshot intact for every window.
  - `test_snapshot_dates_returns_newest_first_per_window`.
  - `test_load_snapshot_round_trips_ranked_players`: z, total, and all ten ranks survive.
  - `test_load_snapshot_missing_date_returns_empty_list`.
- [ ] **Step 2: Run** `uv run pytest tests/adapters/test_repository.py -v`; expect FAIL.
- [ ] **Step 3: Implement** `Settings` and `Repository` with stdlib `sqlite3`, parameterized SQL only, `PRAGMA journal_mode=WAL`. Create `.env.example` listing both variables with their defaults.
- [ ] **Step 4: Run** pytest and ruff; expect pass.
- [ ] **Step 5: Commit** `feat(adapters): add SQLite repository and typed settings`.

---

### Task 4: DataProvider protocol and nba_api adapter

**Files:**
- Create: `src/nba_projector/adapters/provider.py`, `adapters/nba_api_provider.py`, `tests/fixtures/game_logs.json`, `tests/fixtures/schedule.json`
- Modify: `pyproject.toml` (`uv add nba_api`)
- Test: `tests/adapters/test_nba_api_provider.py`

**Interfaces:**
- Consumes: `GameLog` from Task 1.
- Produces:
  - `@dataclass(frozen=True) ScheduledGame`: `game_id: str`, `start_utc: datetime` (timezone-aware), `is_final: bool`.
  - `class DataProvider(Protocol)`: `fetch_game_logs(self, season: str) -> list[GameLog]`; `fetch_schedule(self, game_date: date) -> list[ScheduledGame]` (`game_date` is the US Eastern game date).
  - `parse_game_logs(payload: dict[str, Any], season: str) -> list[GameLog]` and `parse_schedule(payload: dict[str, Any], game_date: date) -> list[ScheduledGame]` (pure).
  - `call_with_retry(fn: Callable[[], T], max_retries: int, base_delay: float, sleep: Callable[[float], None] = time.sleep) -> T` (exponential backoff; re-raises the last error).
  - `class NbaApiProvider` implementing `DataProvider`: `__init__(self, raw_dir: Path, timeout: float = 30.0, max_retries: int = 3, throttle_seconds: float = 0.6)`; writes each raw response to `raw_dir/game_logs-<season>.json` or `raw_dir/schedule-<date>.json` (overwrite).

- [ ] **Step 1: Record fixtures once, outside the repo test run.** In the scratchpad, run a one-off script with the installed `nba_api` that calls the player game log endpoint for the whole league (`LeagueGameLog` with `player_or_team_abbreviation="P"`) and a schedule endpoint that gives UTC start time and final status per game (candidates: `ScheduleLeagueV2`, or `ScoreboardV2`; verify the field names in the installed version before choosing). Save a trimmed copy (about 20 rows; include one traded player with two teams, and one game with status final and one not) as the two fixture files. Record in the module docstring which endpoints and fields were used.
- [ ] **Step 2: Write failing tests**
  - `test_parse_game_logs_maps_fields_to_gamelog`: first fixture row maps to the exact `GameLog` values (assert every field for one row).
  - `test_parse_game_logs_skips_rows_with_null_minutes_or_stats`: rows with missing values are skipped and counted in a log message (assert via `caplog`).
  - `test_parse_game_logs_raises_on_missing_expected_columns`: removing a required column raises a specific `ProviderSchemaError`.
  - `test_parse_schedule_returns_aware_utc_start_and_final_flag`.
  - `test_call_with_retry_succeeds_after_transient_failures`: fails twice, passes third; `sleep` called with `[base, base*2]`.
  - `test_call_with_retry_raises_after_max_retries`.
- [ ] **Step 3: Run** `uv run pytest tests/adapters/test_nba_api_provider.py -v`; expect FAIL.
- [ ] **Step 4: Implement** the parsers, `ProviderSchemaError`, `call_with_retry`, and `NbaApiProvider` (every endpoint call passes `timeout`, goes through `call_with_retry`, and sleeps `throttle_seconds` between calls). Player identity is the upstream player id; no name matching.
- [ ] **Step 5: Run** pytest and ruff; expect pass.
- [ ] **Step 6: Commit** `feat(adapters): add DataProvider protocol and nba_api provider`.

---

### Task 5: Refresh job

**Files:**
- Create: `src/nba_projector/jobs/__init__.py`, `jobs/refresh.py`
- Test: `tests/jobs/test_refresh.py` (use a fake `DataProvider` and a real `Repository` on `tmp_path`)

**Interfaces:**
- Consumes: `DataProvider`, `ScheduledGame` (Task 4); `Repository`, `Settings` (Task 3); `average_logs`, `blend_with_prior`, `season_for`, `rank_players`, `MIN_GAMES`, `MIN_MINUTES_PG` (Task 1).
- Produces:
  - `refresh_deadline(games: Sequence[ScheduledGame], buffer: timedelta = timedelta(hours=3)) -> datetime | None` (latest start plus buffer; `None` if no games).
  - `build_snapshots(logs: Sequence[GameLog], as_of: date) -> dict[Window, list[RankedPlayer]]` (pure; Season window blends the prior season, and includes prior-only players only while the league's maximum games played this season is below 5).
  - `run_refresh(provider: DataProvider, repo: Repository, game_date: date, *, wait: bool = True, now: Callable[[], datetime] = ..., sleep: Callable[[float], None] = time.sleep, poll_seconds: float = 600, cutoff: timedelta = timedelta(hours=6)) -> bool` (returns `True` if a snapshot was written).
  - CLI: `uv run python -m nba_projector.jobs.refresh [--date YYYY-MM-DD] [--no-wait]`; default date is today in `America/New_York`; exit code non-zero on failure.

- [ ] **Step 1: Write failing tests**
  - `test_refresh_deadline_is_latest_start_plus_three_hours`; `test_refresh_deadline_returns_none_without_games`.
  - `test_build_snapshots_has_all_three_windows`.
  - `test_build_snapshots_season_opener_includes_prior_only_players`: logs contain only prior-season games and `as_of` is in the new season: Season snapshot is non-empty.
  - `test_build_snapshots_excludes_prior_only_players_once_season_is_underway`: a player with no current games is absent once another player has at least 5.
  - `test_run_refresh_no_games_writes_nothing_and_returns_false`.
  - `test_run_refresh_waits_until_deadline_then_polls_until_final`: fake `now`/`sleep`; assert sleep durations and that the snapshot is written once all games are final.
  - `test_run_refresh_stops_polling_at_cutoff_and_writes_with_final_games`: one game never goes final; snapshot is still written and a warning is logged.
  - `test_run_refresh_rerun_same_date_does_not_duplicate`: run twice; one snapshot for that date.
  - `test_run_refresh_fetch_failure_leaves_previous_snapshot_intact`: provider raises on the second run; first run's snapshot is unchanged and the exception propagates.
- [ ] **Step 2: Run** `uv run pytest tests/jobs/test_refresh.py -v`; expect FAIL.
- [ ] **Step 3: Implement** the functions above. `run_refresh` order: fetch schedule, return `False` if empty, wait (unless `wait=False`), poll for final, fetch current and prior season logs (prior only if missing from the repository), save logs, `build_snapshots`, `replace_snapshots`. Log each step at INFO with counts; configure logging in the CLI only.
- [ ] **Step 4: Run** pytest and ruff; expect pass.
- [ ] **Step 5: Commit** `feat(jobs): add daily refresh job gated on last game of the day`.

---

### Task 6: Rankings API

**Files:**
- Create: `src/nba_projector/api/schemas.py`
- Modify: `src/nba_projector/api/routes.py`, `pyproject.toml` (`uv add --dev httpx2`; confirm it resolves, since Starlette's `TestClient` requires it)
- Test: `tests/api/test_rankings.py` (override the repository dependency with a `tmp_path` repository)

**Interfaces:**
- Consumes: `Repository` (Task 3), `compute_trends`, `Trend` (Task 2), `Window`, `SORT_KEYS` (Task 1).
- Produces:
  - `get_repository() -> Repository` FastAPI dependency (built from `get_settings()`).
  - `GET /api/v1/rankings?window=season|l15|l30&sort=overall|pts|reb|ast|stl|blk|fg3m|fg_pct|ft_pct|tov&limit=100` (defaults `season`, `overall`, 100; `limit` 1 to 500).
  - `RankingRow`: `rank: int`, `trend: int | None`, `is_new: bool`, `player_id: int`, `player_name: str`, `total: float`, `z: dict[str, float]`.
  - `RankingsResponse`: `window: Window`, `sort: str`, `as_of: date`, `is_stale: bool`, `rows: list[RankingRow]`.
  - Behavior: `rank` and `trend` are for the selected `sort` key; rows ordered by that rank; trend compares with the most recent earlier snapshot for the same window; `is_stale` is true when `as_of` is more than one day before today in `America/New_York`; no snapshot returns 404 `{"detail": "No rankings available yet"}`.

- [ ] **Step 1: Write failing tests**
  - `test_rankings_returns_rows_ordered_by_overall_rank_with_as_of`.
  - `test_rankings_sort_by_category_uses_category_rank_and_trend`.
  - `test_rankings_trend_compares_with_most_recent_earlier_snapshot`: previous snapshot two days earlier still gives a trend.
  - `test_rankings_single_snapshot_has_null_trend_and_not_new`; `test_rankings_player_missing_from_previous_is_new`.
  - `test_rankings_window_param_selects_window_snapshot`.
  - `test_rankings_empty_snapshot_returns_200_with_empty_rows`.
  - `test_rankings_no_snapshot_returns_404`.
  - `test_rankings_invalid_window_or_sort_returns_422`; `test_rankings_limit_caps_rows`.
  - `test_rankings_marks_old_snapshot_stale` (patch "today" through an injectable clock dependency, not the module under test).
- [ ] **Step 2: Run** `uv run pytest tests/api/test_rankings.py -v`; expect FAIL.
- [ ] **Step 3: Implement** schemas and the route. Keep the handler thin; put the rank/trend assembly in a small pure helper in `schemas.py` or `api/rankings.py`.
- [ ] **Step 4: Run** pytest and ruff; expect pass.
- [ ] **Step 5: Commit** `feat(api): add rankings endpoint with window, sort, and trend`.

---

### Task 7: Dashboard page and docs

**Files:**
- Create: `src/nba_projector/web/index.html`, `web/app.js`, `web/style.css`
- Modify: `src/nba_projector/main.py` (serve `/` and `/static`), `README.md`, `.env.example` (if not already complete)
- Test: `tests/test_main.py` (root serves the page)

**Interfaces:**
- Consumes: `GET /api/v1/rankings` (Task 6).
- Produces: `GET /` returns the HTML page; static files under `/static`.

- [ ] **Step 1: Write failing test** `test_root_serves_dashboard_html`: status 200, content type `text/html`, body contains the text "Rankings are statistical estimates, not guarantees."
- [ ] **Step 2: Run** `uv run pytest tests/test_main.py -v`; expect FAIL (404).
- [ ] **Step 3: Implement the page** (no framework): header with title and "As of <date>" plus a stale warning when `is_stale`; window selector (Season default, Last 15, Last 30); a table with columns Rank, Trend, Player, nine category columns, Total; z cells colored as a heat map (green positive, red negative, neutral near 0; theme-aware); trend shown as green "▲ n", red "▼ n", grey "–", or "new"; clicking a category header or Total re-fetches with that `sort` and marks the active column; empty and 404 states show a plain message; the disclaimer line in the footer. Fetch only from `/api/v1/rankings`.
- [ ] **Step 4: Serve it** from `main.py` with `FileResponse` for `/` and `StaticFiles` for `/static`.
- [ ] **Step 5: Update `README.md`**: quick start (`uv sync`, run job with `--no-wait` once to load data, start the server), configuration table (the two env vars), how to schedule the job daily (Windows Task Scheduler or cron, around late morning US Eastern; the job waits for the last game itself), project structure, development commands.
- [ ] **Step 6: Run** `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`; expect all pass.
- [ ] **Step 7: Manual check.** Run `uv run python -m nba_projector.jobs.refresh --no-wait` against the real API (needs network and a game day, otherwise expect "no games" and no snapshot; use `--date` with a past game date), then `uv run uvicorn nba_projector.main:app`. In the browser confirm: table renders, window selector changes data, clicking a column re-ranks, trend shows "–" on the first snapshot, disclaimer visible.
- [ ] **Step 8: Commit** `feat(web): add trending dashboard page and usage docs`.
