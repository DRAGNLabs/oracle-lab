// The Agent trace tab: the run's timeline at full width, the selected event in a
// drawer over its right side, and the lane toggle. Arrow keys move between events
// while the tab is open; j and k stay with the rail.

import { showDetail } from "./detail.js";
import { openLegend } from "./legend.js";
import { api, state } from "./state.js";
import { showTimeline } from "./timeline.js";

// Fetch the open target's trace once; the Overview and Test files tabs read it too.
export async function loadTrace() {
  throw new Error("not implemented");
}

export async function renderTrace(panel) {
  throw new Error("not implemented");
}

// Open the trace tab with this harness step's first event selected, as step links do.
export function openStep(step) {
  throw new Error("not implemented");
}

// Arrow keys and Escape while the trace tab is open. True when the key was used.
export function onTraceKey(event) {
  throw new Error("not implemented");
}
