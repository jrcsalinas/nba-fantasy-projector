"use strict";

const state = { window: "season", sort: "overall", view: "stats" };
let requestId = 0;
let lastData = null;

const els = {
  asOf: document.getElementById("as-of"),
  stale: document.getElementById("stale"),
  message: document.getElementById("message"),
  tableWrap: document.getElementById("table-wrap"),
  tableNote: document.getElementById("table-note"),
  rows: document.getElementById("rows"),
};

// A category cell: the per-game stat (or z-score) shaded by the player's z-score.
function categoryCell(category, row) {
  const td = zCell(row.z[category]);
  if (state.view === "stats") td.textContent = formatStat(category, row.stats);
  return td;
}

function extraCell(text, className = "extra") {
  return cell(className, text);
}

function renderRow(row) {
  const stats = row.stats;
  const tr = document.createElement("tr");
  tr.append(
    cell("rank", String(row.rank)),
    trendCell(row),
    cell("player", row.player_name),
    extraCell(stats && stats.games_played !== null ? String(stats.games_played) : "–", "extra minor"),
    extraCell(stats ? stats.minutes.toFixed(1) : "–", "extra minor"),
  );
  for (const category of CATEGORIES) {
    tr.append(categoryCell(category, row));
    if (category === "fg_pct") tr.append(extraCell(stats ? stats.fga.toFixed(1) : "–", "extra volume"));
    if (category === "ft_pct") tr.append(extraCell(stats ? stats.fta.toFixed(1) : "–", "extra volume"));
  }
  const total = zCell(row.total);
  total.classList.add("total");
  tr.append(total);
  return tr;
}

function showMessage(text) {
  els.message.textContent = text;
  els.message.hidden = false;
  els.tableWrap.hidden = true;
  els.tableNote.hidden = true;
  els.stale.hidden = true;
}

function renderRankings(data) {
  lastData = data;
  els.asOf.textContent = `As of ${data.as_of}`;
  els.stale.hidden = !data.is_stale;
  if (data.is_stale) {
    els.stale.textContent =
      `These rankings are from ${data.as_of}. The daily refresh may not have run since.`;
  }
  if (data.rows.length === 0) {
    showMessage("No players qualify for this window yet.");
    els.stale.hidden = !data.is_stale;
    return;
  }
  els.message.hidden = true;
  els.tableWrap.hidden = false;
  els.tableNote.hidden = false;
  els.rows.replaceChildren(...data.rows.map(renderRow));
}

function syncControls() {
  for (const button of document.querySelectorAll("[data-window]")) {
    button.setAttribute("aria-pressed", String(button.dataset.window === state.window));
  }
  for (const button of document.querySelectorAll("[data-view]")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === state.view));
  }
  for (const button of document.querySelectorAll("[data-sort]")) {
    const th = button.closest("th");
    if (button.dataset.sort === state.sort) th.setAttribute("aria-sort", "ascending");
    else th.removeAttribute("aria-sort");
  }
}

async function load() {
  const id = ++requestId;
  syncControls();
  const params = new URLSearchParams({ window: state.window, sort: state.sort, limit: "100" });
  try {
    const response = await fetch(`/api/v1/rankings?${params}`);
    if (id !== requestId) return;
    if (response.status === 404) {
      els.asOf.textContent = "No data yet";
      showMessage("No rankings yet. Run the refresh job to load data.");
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderRankings(await response.json());
  } catch (error) {
    if (id !== requestId) return;
    console.error(error);
    els.asOf.textContent = "Unavailable";
    showMessage("Couldn't load the rankings. Reload the page to try again.");
  }
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.view) {
    // Same data, different cell text: no need to fetch again.
    state.view = target.dataset.view;
    syncControls();
    if (lastData) renderRankings(lastData);
    return;
  }
  if (target.dataset.window) state.window = target.dataset.window;
  else if (target.dataset.sort) state.sort = target.dataset.sort;
  else return;
  load();
});

load();
