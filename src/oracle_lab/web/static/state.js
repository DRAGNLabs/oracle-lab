// What the page knows, and how it asks the server for more.

export const state = {
  listing: null, // GET /api/targets: every target and this rater's progress
  target: null, // GET /api/target: the target under review, or null on the list
  ratings: {}, // this rater's ratings of the open target, saved or not
  selected: 0, // the question the keyboard rates
  tab: "overview",
  repair: [], // the open target's repair, parsed
  trees: {}, // each repository view's file list, fetched on first use
  browser: null, // the Files tab: view, open file, and filters
  evidence: null, // the evidence tab: open file, its text, and the search
};

export async function api(path, params = {}, options = {}) {
  const query = new URLSearchParams(params).toString();
  const response = await fetch(query ? `${path}?${query}` : path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed: ${response.status}`);
  return body;
}
