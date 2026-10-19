// The timeline: one row per event, one column per lane. Lanes are action types by
// default, or files (patched first, then tests, then the rest) when toggled.

export function showTimeline(container, trace, { lanesByFile, selected, onSelect }) {
  throw new Error("todo");
}

// Lane keys in display order for the current mode.
function lanes(trace, lanesByFile) {
  throw new Error("todo");
}
