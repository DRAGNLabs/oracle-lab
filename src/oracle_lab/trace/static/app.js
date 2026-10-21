// The trace viewer: one run's agent trace as a timeline of typed, annotated events.
// This entry point owns the run picker, the lane toggle, the panel tabs, the hover
// descriptions, and the page state. Plain JavaScript modules, no build step.

import { showDetail } from "./detail.js";
import { esc } from "./html.js";
import { showLegend } from "./legend.js";
import { showSummary } from "./summary.js";
import { showTests } from "./tests.js";
import { showTimeline } from "./timeline.js";

export const state = {
  runs: [], // GET /api/runs: every run's summary
  trace: null, // GET /api/run: the open run's RunTrace
  lanesByFile: false, // default: one lane per action type
  selected: null, // index into trace.events
  panel: "summary", // run tabs: "summary", "tests", "legend"; step tab: "event"
};

export async function api(path, params = {}) {
  const query = new URLSearchParams(params).toString();
  const response = await fetch(query ? `${path}?${query}` : path);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed: ${response.status}`);
  return body;
}

const picker = document.getElementById("run-picker");
const lanesToggle = document.getElementById("lanes-by-file");

picker.addEventListener("change", () => (location.hash = `run=${picker.value}`));
lanesToggle.addEventListener("change", () => {
  state.lanesByFile = lanesToggle.checked;
  render();
});
for (const tab of document.querySelectorAll("[data-panel]")) {
  tab.addEventListener("click", () => {
    state.panel = tab.dataset.panel;
    render();
  });
}
document.addEventListener("keydown", onKey);
document.addEventListener("mouseover", showTip);
window.addEventListener("hashchange", route);
start();

async function start() {
  try {
    state.runs = await api("/api/runs");
  } catch (error) {
    document.getElementById("timeline").innerHTML = `<p class="error">${esc(error.message)}</p>`;
    return;
  }
  picker.innerHTML = pickerOptions(state.runs);
  route();
}

// Runs grouped by instance, each labeled by harness and model.
function pickerOptions(runs) {
  const groups = new Map();
  for (const run of runs) {
    const key = run.instance || "Unreadable runs";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(run);
  }
  return [...groups]
    .map(([instance, members]) => {
      const options = members
        .map((run) => {
          const label = run.error
            ? `${run.run_id} (cannot read: ${run.error})`
            : `${run.harness} · ${shortModel(run.model)} · ${run.run_id}`;
          return `<option value="${esc(run.run_id)}"${run.error ? " disabled" : ""}>${esc(label)}</option>`;
        })
        .join("");
      return `<optgroup label="${esc(instance)}">${options}</optgroup>`;
    })
    .join("");
}

function shortModel(model) {
  return model.split("@")[0].split("/").pop();
}

async function route() {
  const runId = new URLSearchParams(location.hash.slice(1)).get("run") ||
    state.runs.find((run) => !run.error)?.run_id;
  if (!runId) return;
  picker.value = runId;
  if (state.trace?.run.run_id === runId) return;
  document.getElementById("timeline").innerHTML = `<p class="muted">Loading ${esc(runId)}…</p>`;
  try {
    state.trace = await api("/api/run", { id: runId });
  } catch (error) {
    document.getElementById("timeline").innerHTML = `<p class="error">${esc(error.message)}</p>`;
    return;
  }
  state.selected = null;
  if (state.panel === "event") state.panel = "summary";
  render();
}

export function select(index, { scroll = false } = {}) {
  state.selected = index;
  state.panel = "event";
  render();
  if (scroll) {
    document.querySelector(`[data-event="${index}"]`)?.scrollIntoView({ block: "center" });
  }
}

// Select the first event of a harness step, e.g. from the test file panel.
export function selectStep(step) {
  const index = state.trace.events.findIndex((event) => event.step === step);
  if (index >= 0) select(index, { scroll: true });
}

// Redraw everything that depends on state; each view reads state, none writes it.
export function render() {
  const trace = state.trace;
  if (!trace) return;
  showTimeline(document.getElementById("timeline"), trace, {
    lanesByFile: state.lanesByFile,
    selected: state.selected,
    onSelect: select,
  });
  for (const tab of document.querySelectorAll("[data-panel]")) {
    tab.classList.toggle("active", tab.dataset.panel === state.panel);
    if (tab.dataset.panel === "event") {
      tab.textContent = state.selected === null ? "Event" : `Event · step ${trace.events[state.selected].step}`;
    }
  }
  const panel = document.getElementById("panel-body");
  panel.scrollTop = 0;
  if (state.panel === "summary") showSummary(panel, trace, { onSelectStep: selectStep });
  else if (state.panel === "tests") showTests(panel, trace, { onSelectStep: selectStep });
  else if (state.panel === "legend") showLegend(panel);
  else showDetail(panel, trace, state.selected);
}

// One floating description for whatever element under the cursor has `data-tip`.
const tooltip = document.getElementById("tooltip");
function showTip(event) {
  const target = event.target.closest?.("[data-tip]");
  if (!target) {
    tooltip.hidden = true;
    return;
  }
  tooltip.textContent = target.dataset.tip;
  tooltip.hidden = false;
  const box = target.getBoundingClientRect();
  const left = Math.min(box.left, window.innerWidth - tooltip.offsetWidth - 8);
  const below = box.bottom + 6 + tooltip.offsetHeight < window.innerHeight;
  tooltip.style.left = `${Math.max(left, 8)}px`;
  tooltip.style.top = `${below ? box.bottom + 6 : box.top - tooltip.offsetHeight - 6}px`;
}

function onKey(event) {
  if (!state.trace || event.target.matches("input, select, textarea")) return;
  const count = state.trace.events.length;
  if (event.key === "j" || event.key === "ArrowDown") {
    select(Math.min((state.selected ?? -1) + 1, count - 1), { scroll: true });
  } else if (event.key === "k" || event.key === "ArrowUp") {
    select(Math.max((state.selected ?? 1) - 1, 0), { scroll: true });
  } else {
    return;
  }
  event.preventDefault();
}
