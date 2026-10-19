// The trace viewer: one run's agent trace as a timeline of typed, annotated events.
// This entry point owns the run picker, the two toggles, and the page state.
// Plain JavaScript modules, no build step.

import { showDetail } from "./detail.js";
import { showTests } from "./tests.js";
import { showTimeline } from "./timeline.js";

export const state = {
  runs: [], // GET /api/runs: every run's summary
  trace: null, // GET /api/run: the open run's RunTrace
  lanesByFile: false, // default: one lane per action type
  showOutcomes: false, // final evaluation results stay hidden until asked for
  selected: null, // the selected event's [step, part]
};

export async function api(path, params = {}) {
  throw new Error("todo");
}

async function openRun(runId) {
  throw new Error("todo");
}

// Redraw everything that depends on state; each view reads state, none writes it.
export function render() {
  throw new Error("todo");
}
