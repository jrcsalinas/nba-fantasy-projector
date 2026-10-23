# My Roster Tab: Design

Date: 2026-10-23. Status: draft, awaiting approval.

## Intent and why

A personal page, separate from the trending dashboard, that answers two questions: **how is my
team doing in each of the nine categories**, and **who could I pick up to fix the weak ones**.
It reuses the dashboard's saved ranking snapshots, so it needs no new data fetching.

## Goals

- A **My Roster** tab at `/roster`, linked from the dashboard and back.
- A grid of my players in the same layout as the dashboard: Rank, Trend, Player, nine category
  z-scores (heat map), Total. Same window selector (Season, Last 15, Last 30) and column sorting.
- IL players appear in their own group and are left out of team totals.
- A **team row** summing the active players' z-scores per category, so strengths and weak
  categories are visible at a glance.
- **Waiver suggestions** for the three weakest categories: the best players outside my roster
  in that category, with their overall rank, total, and the category z-score that explains
  the pick.

## Non-goals

- Connecting to ESPN or Yahoo, or knowing my league's real free agents.
- Drop suggestions, trades, start/sit, or multi-team support.
- Editing the roster in the browser.

## Decisions made by the user

- Roster: Tyrese Maxey, Kevin Durant, Donovan Clingan, LeBron James, Trey Murphy III,
  Peyton Watson, Cason Wallace, Rudy Gobert, Isaiah Hartenstein, Tyler Herro, Dennis Schroder,
  Davion Mitchell, Draymond Green. IL: Coby White.
- Scope: grid plus waiver suggestions.
- Availability: **rank cutoff**. A player outside the top N overall (default 150) counts as
  likely available. This is an estimate, and the page says so.

## Design

- **Roster storage:** `data/roster.json`, listing `active` and `il` as `{player_id, name}`.
  `data/` is gitignored, so the roster stays personal. Path set by `NBA_PROJECTOR_ROSTER_PATH`.
  Matching is by `player_id`; `name` is only for readability.
- **Domain (pure):** `team_totals(players)` sums z per category over active players.
  `weakest_categories(totals, n=3)` picks the lowest sums. `waiver_candidates(snapshot, roster_ids,
  category, cutoff, limit)` returns non-roster players ranked below `cutoff` overall, ordered by
  that category's z-score.
- **API:** `GET /api/v1/roster?window=&sort=` returns rows with `slot` (`active` or `il`), the
  team totals, and the weak categories. `GET /api/v1/roster/waivers?window=&cutoff=150&limit=5`
  returns candidates per weak category. Both read saved snapshots only.
- **Roster player missing from the snapshot** (below the games or minutes threshold): the row
  shows with no z-scores and the note "Not ranked in this window".
- **Missing roster file:** the API returns 404 `{"detail": "Add data/roster.json"}`; the page
  shows that message.
- **Trend** uses the same rule as the dashboard.

## Tasks

1. Roster domain: totals, weakest categories, waiver candidates (pure, tested).
2. Roster file loading and settings.
3. Roster and waivers API.
4. `/roster` page with navigation between the two tabs, README update.

One commit and push per task, as before.
