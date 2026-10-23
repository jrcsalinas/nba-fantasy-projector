"use strict";

const TEAM_HEAT_Z = 8;
const DEFAULT_CUTOFF = 150;

const state = { window: "season", sort: "overall", cutoff: DEFAULT_CUTOFF };
let requestId = 0;

const els = {
  asOf: document.getElementById("as-of"),
  stale: document.getElementById("stale"),
  message: document.getElementById("message"),
  tableWrap: document.getElementById("table-wrap"),
  rows: document.getElementById("rows"),
  waivers: document.getElementById("waivers"),
  waiverNote: document.getElementById("waiver-note"),
  waiverMessage: document.getElementById("waiver-message"),
  waiverGroups: document.getElementById("waiver-groups"),
  cutoff: document.getElementById("cutoff"),
};

function rankedRow(row) {
  const tr = document.createElement("tr");
  tr.append(cell("rank", String(row.rank)), trendCell(row), cell("player", row.player_name));
  for (const category of CATEGORIES) tr.append(zCell(row.z[category]));
  const total = zCell(row.total);
  total.classList.add("total");
  tr.append(total);
  return tr;
}

function unrankedRow(row) {
  const tr = document.createElement("tr");
  tr.append(cell("rank", "–"), cell("trend-flat", "–"), cell("player", row.player_name));
  const note = cell("muted", "Not ranked in this window");
  note.colSpan = CATEGORIES.length + 1;
  note.style.textAlign = "left";
  tr.append(note);
  return tr;
}

function teamRow(data) {
  const tr = document.createElement("tr");
  tr.className = "team";
  tr.append(cell("", ""), cell("", ""), cell("player", "Team (active players)"));
  let grandTotal = 0;
  for (const category of CATEGORIES) {
    const sum = data.team[category];
    grandTotal += sum;
    const td = zCell(sum, TEAM_HEAT_Z);
    if (data.weak_categories.includes(category)) td.classList.add("weak");
    tr.append(td);
  }
  const total = zCell(grandTotal, TEAM_HEAT_Z * 3);
  total.classList.add("total");
  tr.append(total);
  return tr;
}

function groupRow(label) {
  const tr = document.createElement("tr");
  tr.className = "group";
  const th = document.createElement("th");
  th.colSpan = CATEGORIES.length + 4;
  th.scope = "colgroup";
  th.textContent = label;
  tr.append(th);
  return tr;
}

function renderRosterRows(data) {
  const toRow = (row) => (row.ranked ? rankedRow(row) : unrankedRow(row));
  const active = data.rows.filter((row) => row.slot === "active").map(toRow);
  const il = data.rows.filter((row) => row.slot === "il").map(toRow);
  const rows = [...active];
  if (Object.keys(data.team).length > 0) rows.push(teamRow(data));
  if (il.length > 0) rows.push(groupRow("Injured list (not counted in team row)"), ...il);
  els.rows.replaceChildren(...rows);
}

function showMessage(text) {
  els.message.textContent = text;
  els.message.hidden = false;
  els.tableWrap.hidden = true;
  els.waivers.hidden = true;
  els.stale.hidden = true;
}

function renderRoster(data) {
  els.asOf.textContent = `As of ${data.as_of}`;
  els.stale.hidden = !data.is_stale;
  if (data.is_stale) {
    els.stale.textContent =
      `These rankings are from ${data.as_of}. The daily refresh may not have run since.`;
  }
  els.message.hidden = true;
  els.tableWrap.hidden = false;
  renderRosterRows(data);
}

function waiverGroup(group) {
  const section = document.createElement("div");
  section.className = "waiver-group";

  const heading = document.createElement("h3");
  heading.textContent = CATEGORY_LABELS[group.category];
  const total = document.createElement("span");
  total.className = "muted";
  total.textContent = ` team total ${formatZ(group.team_total)}`;
  heading.append(total);
  section.append(heading);

  const table = document.createElement("table");
  const head = table.createTHead().insertRow();
  for (const label of ["Player", "Overall rank", CATEGORY_LABELS[group.category], "Total"]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    head.append(th);
  }
  head.cells[0].className = "player";

  const body = table.createTBody();
  for (const candidate of group.candidates) {
    const tr = body.insertRow();
    tr.append(
      cell("player", candidate.player_name),
      cell("", String(candidate.overall_rank)),
      zCell(candidate.category_z),
      cell("total", formatZ(candidate.total)),
    );
  }
  if (group.candidates.length === 0) {
    const td = body.insertRow().insertCell();
    td.colSpan = 4;
    td.className = "muted";
    td.textContent = "No players outside the cutoff.";
  }
  section.append(table);
  return section;
}

function renderWaivers(data) {
  els.waivers.hidden = false;
  els.waiverNote.textContent = data.availability_note;
  els.waiverMessage.hidden = data.suggestions.length > 0;
  if (data.suggestions.length === 0) {
    els.waiverMessage.textContent = "No suggestions until some of your players are ranked.";
  }
  els.waiverGroups.replaceChildren(...data.suggestions.map(waiverGroup));
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
  const rosterParams = new URLSearchParams({ window: state.window, sort: state.sort });
  const waiverParams = new URLSearchParams({ window: state.window, cutoff: state.cutoff });
  try {
    const [rosterResponse, waiverResponse] = await Promise.all([
      fetch(`/api/v1/roster?${rosterParams}`),
      fetch(`/api/v1/roster/waivers?${waiverParams}`),
    ]);
    if (id !== requestId) return;
    if (!rosterResponse.ok) {
      els.asOf.textContent = "Unavailable";
      showMessage(await errorDetail(rosterResponse));
      return;
    }
    renderRoster(await rosterResponse.json());
    if (waiverResponse.ok) renderWaivers(await waiverResponse.json());
    else els.waivers.hidden = true;
  } catch (error) {
    if (id !== requestId) return;
    console.error(error);
    els.asOf.textContent = "Unavailable";
    showMessage("Couldn't load your roster. Reload the page to try again.");
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

els.cutoff.addEventListener("change", () => {
  const value = Number.parseInt(els.cutoff.value, 10);
  state.cutoff = Number.isFinite(value) ? Math.min(Math.max(value, 1), 500) : DEFAULT_CUTOFF;
  els.cutoff.value = String(state.cutoff);
  load();
});

load();
