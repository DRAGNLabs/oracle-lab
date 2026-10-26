// What the page knows, and how it asks the server for more.

export const state = {
  listing: null, // GET /api/targets: every problem, its tasks, and this rater's progress
  problem: null, // GET /api/problem: the open target's problem, shown by the Problem tab
  target: null, // GET /api/target: the target under review, or null off a rating page
  ratings: {}, // this rater's ratings of the open target, saved or not
  otherLabels: [], // this rater's own failure-mode labels for the open target
  selected: 0, // the question the keyboard rates
  tab: "overview", // the middle tab
  sideTab: "problem", // the sidebar tab
  repair: [], // the open target's repair, parsed
  trees: {}, // each repository view's file list, fetched on first use
  browser: null, // the Files tab: view, open file, and filters
  evidence: null, // the evidence tab: open file, its text, and the search
  trace: null, // GET /api/trace: the open judgment's RunTrace, fetched on first use
  // The Agent trace tab: the selected event, the lane mode, and a step a link asked to open.
  traceView: { selected: null, lanesByFile: false, pendingStep: null },
};

export async function api(path, params = {}, options = {}) {
  const query = new URLSearchParams(params).toString();
  const response = await fetch(query ? `${path}?${query}` : path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed: ${response.status}`);
  return body;
}
