// The timeline: one row per event, one narrow column per lane, then the description.
// Lanes are action types by default, or files (patched first, then tests, then the
// rest) when toggled. Rows touching patched lines are marked strongly, rows touching
// only a patched file faintly.

import { ACTION_LABELS, editFacts, esc } from "./html.js";

const NO_FILE = "(no file)";
const OTHER_FILES = "(files touched once)";

export function showTimeline(container, trace, { lanesByFile, showEditFacts, selected, onSelect }) {
  const laneKeys = lanes(trace, lanesByFile);
  const facts = showEditFacts ? factsByEdit(trace) : new Map();
  const template = `4.5em repeat(${laneKeys.length}, 22px) minmax(0, 1fr)`;
  const header = laneKeys
    .map((key) => {
      // The last two path parts, so `tests/conftest.py` and `oracle_tests/conftest.py` differ.
      const label = lanesByFile ? key.split("/").slice(-2).join("/") : ACTION_LABELS[key];
      const color = lanesByFile ? "" : ` style="--lane: var(--a-${key})"`;
      return `<div class="lane-head${lanesByFile ? "" : " typed"}"${color} title="${esc(key)}"><span>${esc(label)}</span></div>`;
    })
    .join("");
  const rows = trace.events
    .map((event, index) => {
      const lastPart = trace.events[index + 1]?.step !== event.step;
      const labels = facts.get(`${event.step}:${event.part}`) || [];
      return row(event, index, laneKeys, lanesByFile, index === selected, lastPart, labels);
    })
    .join("");
  container.innerHTML = `
    ${legend()}
    <div class="grid" style="grid-template-columns: ${template}">
      <div class="lane-head step-head">Step</div>${header}<div class="lane-head desc-head"></div>
      ${rows}
    </div>`;
  for (const element of container.querySelectorAll("[data-event]")) {
    element.addEventListener("click", () => onSelect(Number(element.dataset.event)));
  }
}

function row(event, index, laneKeys, lanesByFile, isSelected, lastPart, factLabels) {
  const inLanes = new Set(lanesByFile ? fileLanes(event, laneKeys) : [event.action]);
  const cells = laneKeys
    .map((key) => `<div class="cell">${inLanes.has(key) ? `<span class="dot" style="--dot: var(--a-${event.action})"></span>` : ""}</div>`)
    .join("");
  const overlap = strongestOverlap(event);
  const classes = ["row", `overlap-${overlap}`, event.part ? "part" : "", isSelected ? "selected" : ""].join(" ");
  const step = event.part ? "↳" : event.step;
  const marks = [
    event.git_history ? `<span class="tag">git history</span>` : "",
    event.patched_names.length ? `<span class="tag patched" title="Mentions names defined in patched hunks">${esc(event.patched_names.join(", "))}</span>` : "",
    // A chained command's exit code belongs to the whole chain; show it once, at the end.
    event.exit_code && lastPart ? `<span class="tag">exit ${event.exit_code}</span>` : "",
    ...factLabels.map((label) => `<span class="tag fact">${esc(label)}</span>`),
    ...categories(event).map((category) => `<span class="tag cat-${category}">${category.replace("_", " ")}</span>`),
  ].join("");
  const expanded = isSelected && (event.action === "reasoning" || event.action === "message")
    ? `<div class="full-text">${esc(event.text)}</div>`
    : "";
  return `
    <div class="${classes}" data-event="${index}">
      <div class="step">${step}</div>${cells}
      <div class="desc ${event.action}">
        <span class="desc-text">${esc(event.description)}</span>${marks}${expanded}
      </div>
    </div>`;
}

// Edit fact labels by the "step:part" of the edit that made each test file version.
function factsByEdit(trace) {
  const facts = new Map();
  for (const file of trace.generated_files) {
    for (const version of file.versions) {
      const labels = editFacts(version);
      if (labels.length) facts.set(`${version.step}:${version.part}`, labels);
    }
  }
  return facts;
}

// Lane keys in display order for the current mode.
function lanes(trace, lanesByFile) {
  if (!lanesByFile) {
    const used = new Set(trace.events.map((event) => event.action));
    return Object.keys(ACTION_LABELS).filter((key) => used.has(key));
  }
  const counts = new Map();
  const rank = new Map(); // 0: patched, 1: test, 2: other
  for (const event of trace.events) {
    for (const touch of event.touches) {
      counts.set(touch.path, (counts.get(touch.path) || 0) + 1);
      const fileRank = touch.overlap !== "none" ? 0
        : touch.category === "generated_test" || touch.category === "test" ? 1 : 2;
      rank.set(touch.path, Math.min(rank.get(touch.path) ?? 2, fileRank));
    }
  }
  const files = [...counts.keys()].filter((path) => rank.get(path) < 2 || counts.get(path) > 1);
  files.sort((a, b) => rank.get(a) - rank.get(b));
  const keys = [...files];
  if (files.length < counts.size) keys.push(OTHER_FILES);
  keys.push(NO_FILE);
  return keys;
}

function fileLanes(event, laneKeys) {
  if (!event.touches.length) return [NO_FILE];
  return event.touches.map((touch) => (laneKeys.includes(touch.path) ? touch.path : OTHER_FILES));
}

function strongestOverlap(event) {
  const levels = event.touches.map((touch) => touch.overlap);
  return levels.includes("lines") ? "lines" : levels.includes("file") ? "file" : "none";
}

// Categories worth flagging on a row: everything except plain source and generated tests.
function categories(event) {
  const shown = new Set(["test", "docs", "changelog", "config", "outside"]);
  return [...new Set(event.touches.map((touch) => touch.category))].filter((category) => shown.has(category));
}

function legend() {
  const items = Object.entries(ACTION_LABELS)
    .map(([key, label]) => `<span class="legend-item"><span class="dot" style="--dot: var(--a-${key})"></span>${label}</span>`)
    .join("");
  return `<div class="legend">${items}
    <span class="legend-item"><span class="swatch overlap-lines"></span>Patched lines</span>
    <span class="legend-item"><span class="swatch overlap-file"></span>Patched file</span>
  </div>`;
}
