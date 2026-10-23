"use strict";

const CATEGORIES = ["pts", "reb", "ast", "stl", "blk", "fg3m", "fg_pct", "ft_pct", "tov"];
const HEAT_SATURATION_Z = 3;

const state = { window: "season", sort: "overall" };
let requestId = 0;

const els = {
  asOf: document.getElementById("as-of"),
  stale: document.getElementById("stale"),
  message: document.getElementById("message"),
  tableWrap: document.getElementById("table-wrap"),
  rows: document.getElementById("rows"),
};

function cell(className, text) {
  const td = document.createElement("td");
  if (className) td.className = className;
  td.textContent = text;
  return td;
}

function trendCell(row) {
  const td = cell("", "–");
  td.className = "trend-flat";
  if (row.is_new) {
    td.textContent = "new";
  } else if (row.trend > 0) {
    td.textContent = `▲ ${row.trend}`;
    td.className = "trend-up";
  } else if (row.trend < 0) {
    td.textContent = `▼ ${Math.abs(row.trend)}`;
    td.className = "trend-down";
  } else if (row.trend === 0) {
    td.textContent = "0";
  }
  return td;
}

function formatZ(z) {
  const text = z.toFixed(1);
  return text === "-0.0" ? "0.0" : text;
}

function zCell(z) {
  const td = cell(z >= 0.05 ? "pos" : z <= -0.05 ? "neg" : "", formatZ(z));
  td.style.setProperty("--heat", Math.min(Math.abs(z) / HEAT_SATURATION_Z, 1).toFixed(2));
  return td;
}

function renderRow(row) {
  const tr = document.createElement("tr");
  tr.append(cell("rank", String(row.rank)), trendCell(row), cell("player", row.player_name));
  for (const category of CATEGORIES) tr.append(zCell(row.z[category]));
  const total = zCell(row.total);
  total.classList.add("total");
  tr.append(total);
  return tr;
}

function showMessage(text) {
  els.message.textContent = text;
  els.message.hidden = false;
  els.tableWrap.hidden = true;
  els.stale.hidden = true;
}

function renderRankings(data) {
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
  els.rows.replaceChildren(...data.rows.map(renderRow));
}

function syncControls() {
  for (const button of document.querySelectorAll("[data-window]")) {
    button.setAttribute("aria-pressed", String(button.dataset.window === state.window));
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
  if (target.dataset.window) state.window = target.dataset.window;
  else if (target.dataset.sort) state.sort = target.dataset.sort;
  else return;
  load();
});

load();
