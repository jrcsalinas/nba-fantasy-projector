"use strict";

const CATEGORIES = ["pts", "reb", "ast", "stl", "blk", "fg3m", "fg_pct", "ft_pct", "tov"];
const CATEGORY_LABELS = {
  pts: "PTS",
  reb: "REB",
  ast: "AST",
  stl: "STL",
  blk: "BLK",
  fg3m: "3PM",
  fg_pct: "FG%",
  ft_pct: "FT%",
  tov: "TO",
};
const PLAYER_HEAT_Z = 3;

function cell(className, text) {
  const td = document.createElement("td");
  if (className) td.className = className;
  td.textContent = text;
  return td;
}

function trendCell(row) {
  const td = cell("trend-flat", "–");
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

// A z-score cell shaded green or red; the shade is full strength at `saturation`.
function zCell(z, saturation = PLAYER_HEAT_Z) {
  const td = cell(z >= 0.05 ? "pos" : z <= -0.05 ? "neg" : "", formatZ(z));
  td.style.setProperty("--heat", Math.min(Math.abs(z) / saturation, 1).toFixed(2));
  return td;
}

// The API's error `detail` when it sent one, otherwise a generic message.
async function errorDetail(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Not JSON; fall through to the generic message.
  }
  return `The server returned an error (HTTP ${response.status}).`;
}

// A category's per-game value as text. Percentages are shown as 48.5, not 0.485.
function formatStat(category, stats) {
  if (!stats) return "–";
  if (category === "fg_pct" || category === "ft_pct") {
    return stats[category] === null ? "–" : (stats[category] * 100).toFixed(1);
  }
  return stats[category].toFixed(1);
}
