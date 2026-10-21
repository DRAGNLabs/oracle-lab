// The trace viewer: one run's agent trace as a timeline of typed, annotated events.
// This entry point owns the run picker, the toggles, the panel tabs, and the page
// state. Plain JavaScript modules, no build step.

import { showDetail } from "./detail.js";
import { esc } from "./html.js";
import { showTests } from "./tests.js";
import { showTimeline } from "./timeline.js";

export const state = {
  runs: [], // GET /api/runs: every run's summary
  trace: null, // GET /api/run: the open run's RunTrace
  lanesByFile: false, // default: one lane per action type
  showOutcomes: false, // final evaluation results stay hidden until asked for
  showEditFacts: false, // mechanical facts about test edits, also hidden until asked for
  selected: null, // index into trace.events
  panel: "event", // "event" or "tests"
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
const outcomesToggle = document.getElementById("show-outcomes");
const editFactsToggle = document.getElementById("show-edit-facts");

picker.addEventListener("change", () => (location.hash = `run=${picker.value}`));
lanesToggle.addEventListener("change", () => {
  state.lanesByFile = lanesToggle.checked;
  render();
});
outcomesToggle.addEventListener("change", () => {
  state.showOutcomes = outcomesToggle.checked;
  render();
});
editFactsToggle.addEventListener("change", () => {
  state.showEditFacts = editFactsToggle.checked;
  render();
});
for (const tab of document.querySelectorAll("[data-panel]")) {
  tab.addEventListener("click", () => {
    state.panel = tab.dataset.panel;
    render();
  });
}
document.addEventListener("keydown", onKey);
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
  outcomesToggle.disabled = !state.trace.outcomes_available;
  if (outcomesToggle.disabled) outcomesToggle.checked = state.showOutcomes = false;
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
  document.getElementById("run-header").innerHTML = runHeader(trace);
  showTimeline(document.getElementById("timeline"), trace, {
    lanesByFile: state.lanesByFile,
    showEditFacts: state.showEditFacts,
    selected: state.selected,
    onSelect: select,
  });
  for (const tab of document.querySelectorAll("[data-panel]")) {
    tab.classList.toggle("active", tab.dataset.panel === state.panel);
  }
  const panel = document.getElementById("panel-body");
  if (state.panel === "tests") {
    showTests(panel, trace, {
      showOutcomes: state.showOutcomes,
      showEditFacts: state.showEditFacts,
      onSelectStep: selectStep,
    });
  } else {
    showDetail(panel, trace, state.selected);
  }
}

function runHeader(trace) {
  const defect = trace.defect;
  const first = defect.first_touch === null ? "never" : `step ${defect.first_touch}`;
  const target =
    defect.target_read === null ? "" : `<span>Target definition shown: <b>${defect.target_read ? "yes" : "no"}</b></span>`;
  const names = defect.names.length
    ? `<span>Patched names: ${defect.names.map((name) => `<code>${esc(name)}</code>`).join(" ")}</span>`
    : "";
  return `
    <div class="run-title">
      <b>${esc(trace.run.instance)}</b>
      <span class="muted">${esc(trace.run.harness)} · ${esc(trace.run.model)}</span>
      <span class="badge" title="Whether the harness records the model's own reasoning or a summary of it">${
        trace.reasoning === "raw" ? "raw reasoning" : "summarized reasoning"
      }</span>
    </div>
    <div class="run-facts">
      <span>First touch of patched lines: <b>${first}</b></span>
      <span title="Changed buggy-side lines whose text appeared in output the agent saw. Truncated output does not count, so this is a lower bound.">Patched lines shown: <b>${defect.lines_shown}/${defect.lines_total}</b></span>
      ${target}
      ${names}
    </div>`;
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
