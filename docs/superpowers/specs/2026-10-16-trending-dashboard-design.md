# Trending Players Dashboard: Design

Date: 2026-10-16. Status: draft, awaiting approval.

## Intent and why

The home page of NBA Fantasy Projector should answer one question each morning: **who is
statistically the best in 9-category fantasy right now, and who is moving up or down?** The user
plays 9-cat (PTS, REB, AST, STL, BLK, 3PM, FG%, FT%, TO) and wants a single overall view first,
then the ability to focus on specific stats.

## Goals

- A home page showing a ranked grid of players with:
  - **Rank**: our own 9-cat ranking.
  - **Trend**: rank change versus yesterday's ranking (up N, down N, unchanged).
  - **Player** name.
  - Per-category z-score cells (PTS, REB, AST, STL, BLK, 3PM, FG%, FT%, TO), colored as a heat map
    (green good, red bad, neutral near zero).
  - **Total** (sum of the nine category z-scores).
- Clicking a category column header re-ranks the grid by that category. Each category also gets
  its own daily ranking, so its trend is the movement in that category's rank since yesterday.
- A **window selector** with three options: **Season** (default), **Last 15 days**, **Last 30
  days**. Every ranking, category rank, and trend on the page is for the selected window.
- Rankings refresh **once per day**, only after the last scheduled game of that day has finished.
- Each day's ranking is saved, so yesterday's ranks are always available for trend.

## Non-goals (this iteration)

- Start/sit advice, waivers, trades, draft board, or rest-of-season projections.
- Points leagues or custom league settings. 9-cat only, fixed categories.
- User accounts, saved leagues, or personalization.
- Real-time or in-game updates.

## Anti-goals

- The page must never call `nba_api` while serving a request. If the data source is down, the
  page still shows the last saved ranking.
- A failed or partial refresh must not overwrite the last good ranking or show half-updated data.

## How the ranking works

- **Windows:** the user chose season-to-date averages as the default, with last-15-days and
  last-30-days as selectable alternatives. This means the job stores game-by-game logs, not just
  season aggregates, and computes a separate ranking snapshot for each window every day.
- **Category score:** per-game average per category (over the selected window) converted to a
  z-score against the player pool. TO is reversed because fewer is better. FG% and FT% are volume-weighted (made-minus-
  expected-makes style) so low-attempt players do not distort them. This is the builder's call on
  the exact formula.
- **Total:** sum of the nine category z-scores. Rank = order by total.
- **Seeding:** the first ranking is built from last season's stats.
- **After that:** rankings use this season's games, with last season's weight shrinking as games
  played grows. The exact blending curve is the builder's call.
- **Trend:** today's rank minus yesterday's rank for the same ranking (overall or a single
  category) and the same window.

## Daily refresh

1. Once a day, a scheduled job fetches the day's schedule and finds the **start time of the last
   scheduled game**.
2. It waits until that game should be finished. Assumption (mine): start time plus roughly three
   hours, then confirm the game is final from the data source before refreshing.
3. After that, it fetches updated stats, computes the rankings, and saves them as that day's
   snapshot in one transaction.
4. Off-days (no games): skip the refresh and keep the last snapshot.
5. The job must be safe to re-run for the same day (idempotent): a re-run replaces that day's
   snapshot rather than adding a duplicate.

## Constraints

- Python 3.12+, FastAPI, managed with uv; ruff for lint and format. Follow the existing layout in
  `src/nba_projector/`; add only folders that have contents.
- Data source: `nba_api`, wrapped behind an adapter so it can be swapped. It is unofficial and
  rate limited: use timeouts, retries with backoff, throttling, and caching, and validate
  responses.
- Storage: SQLite for local development, one table of daily ranking snapshots plus whatever raw
  and cleaned data the builder needs. Raw responses are kept separate from cleaned data so
  processing can be rerun.
- Page: FastAPI serves a JSON endpoint (under `/api/v1/`) and a plain HTML/JavaScript page. No
  frontend framework.
- Domain and ranking code stays pure: no network or database calls.
- Show a short note on the page that rankings are statistical estimates, not guarantees, with no
  betting framing.

## Decisions made by the user

- 9-cat only, overall view first, filter by stat afterward.
- Grid layout with all nine categories visible, plus Rank and Trend columns (mockup B).
- Own ranking built daily, trend is change in rank since yesterday.
- Last season's stats serve as the basis, especially at the start.
- Refresh timing is tied to the last scheduled game of the day.
- Stat window: season-to-date by default, switchable to last 15 or last 30 days.
- Defaults accepted: `nba_api`, SQLite, scheduled daily job, FastAPI with a plain HTML page.

## Left to the builder

- Exact z-score and percentage-stat formulas, and the minimum-games threshold for appearing in
  the ranking.
- The blending curve between last season and this season.
- Scheduler mechanism (cron, APScheduler, or similar) and how the "last game finished" wait works.
- Database schema and table names.
- How many players the grid shows by default, and paging.
- Timezone handling for "the day" (builder's call, but it must be explicit and consistent).

## First-run data

On first run there is no daily history, so every trend is blank until the second snapshot exists.
Show a dash for trend until then.

## Calls made by the designer (not yet confirmed by the user)

- **Last-season blend applies to the Season window only.** The 15-day and 30-day windows use just
  the games inside the window, with a minimum-games threshold (builder's call) so tiny samples do
  not rank.
- **Player pool:** every player with at least a minimum number of games and minutes (threshold is
  the builder's call). The pool is recomputed each day, so a player entering or leaving the pool
  can move others' ranks slightly. Accepted as normal.
- **No prior season (rookies, returners):** ranked on this season alone, using a replacement-level
  prior for the blend. They appear in the grid once they meet the pool threshold.
- **Trend baseline:** compare with the most recent earlier snapshot, not strictly yesterday's
  calendar date, so off-days and failed refreshes do not blank the trend. A player with no earlier
  rank shows "new".
- **Staleness:** the page and API show an "as of" date for the snapshot, plus a stale warning if
  it is more than one game day old.
- **Deployment:** local-first. The refresh job is its own entry point (run with `uv run`) started
  by an external scheduler such as Windows Task Scheduler or cron, not inside the FastAPI process.
  SQLite uses WAL mode so the web app can read while the job writes.
- **Game that never finishes:** keep polling until a cutoff (about 6 hours after the last start
  time). Then refresh using the games that are final and log the unfinished one.
- **Snapshot date:** the date the games were played, in US Eastern time. Games ending after
  midnight still belong to the day they started. One snapshot per game date.
