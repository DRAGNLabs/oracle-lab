// The Agent trace tab: the run's timeline at full width and the lane toggle. The
// selected event shows in the sidebar's Event tab. Arrow keys move between events
// while the tab is open; j and k stay with the rail.

import { showDetail } from "./detail.js";
import { esc } from "./render.js";
import { showSideTab, showTab } from "./review.js";
import { api, state } from "./state.js";
import { showTimeline } from "./timeline.js";

// Fetch the open target's trace once; the Overview and Test files tabs read it too.
export async function loadTrace() {
  const id = state.target.id;
  if (state.trace?.id !== id) state.trace = { id, data: await api("/api/trace", { id }) };
  return state.trace.data;
}

export async function renderTrace(panel) {
  panel.innerHTML = `<p class="message">Loading the agent trace</p>`;
  let trace;
  try {
    trace = await loadTrace();
  } catch (error) {
    panel.innerHTML = `<p class="message error">${esc(error.message)}</p>`;
    return;
  }
  if (state.tab !== "trace") return; // the rater moved on while it loaded
  panel.innerHTML = `
    <div class="trace-tab trace-ui">
      <div class="trace-tools">
        <label><input type="checkbox" id="lanes-by-file" ${state.traceView.lanesByFile ? "checked" : ""}> Lanes by file</label>
        <span class="muted"><kbd>↑</kbd> <kbd>↓</kbd> move between events · <kbd>Esc</kbd> clears the selection · the Legend tab explains every color and tag</span>
      </div>
      <div id="timeline"></div>
    </div>`;
  panel.querySelector("#lanes-by-file").addEventListener("change", (event) => {
    state.traceView.lanesByFile = event.target.checked;
    drawTimeline();
  });
  const pending = state.traceView.pendingStep;
  state.traceView.pendingStep = null;
  if (pending !== null) {
    const index = trace.events.findIndex((event) => event.step === pending);
    select(index >= 0 ? index : null);
    scrollToSelected();
  } else {
    drawTimeline();
  }
}

// The sidebar's Event tab: the selected event in full, or how to select one.
export function renderEvent(panel) {
  const { selected } = state.traceView;
  if (selected === null) {
    panel.innerHTML = `<p class="message">Select an event in the Agent trace tab.</p>`;
    return;
  }
  panel.innerHTML = `<div class="side-doc trace-ui"></div>`;
  showDetail(panel.firstElementChild, state.trace.data, selected);
}

// Open the trace tab with this harness step's first event selected, as step links do.
export function openStep(step) {
  state.traceView.pendingStep = step;
  showTab("trace");
}

// Arrow keys and Escape while the trace tab is open. True when the key was used.
export function onTraceKey(event) {
  const trace = state.trace?.data;
  if (state.tab !== "trace" || !trace || !document.getElementById("timeline")) return false;
  const selected = state.traceView.selected;
  if (event.key === "ArrowDown") {
    select(Math.min((selected ?? -1) + 1, trace.events.length - 1));
  } else if (event.key === "ArrowUp") {
    select(Math.max((selected ?? 1) - 1, 0));
  } else if (event.key === "Escape" && selected !== null) {
    select(null);
    return true;
  } else {
    return false;
  }
  scrollToSelected();
  return true;
}

// Select an event, or none, and show it in the sidebar.
function select(index) {
  state.traceView.selected = index;
  drawTimeline();
  showSideTab(index === null ? state.sideTab : "event");
}

function drawTimeline() {
  const { selected, lanesByFile } = state.traceView;
  showTimeline(document.getElementById("timeline"), state.trace.data, { lanesByFile, selected, onSelect: select });
}

// Rows are `display: contents` and have no box of their own, so scroll to the step cell.
function scrollToSelected() {
  document.querySelector(`#timeline [data-event="${state.traceView.selected}"] .step`)?.scrollIntoView({ block: "center" });
}
