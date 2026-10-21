// The review page: one rater rates one LLM output at a time against its evidence.
// This entry point routes between the home page, a problem page, and a rating page,
// and owns the keyboard, the hover text, and the help dialog. Plain JavaScript
// modules, no build step.

import { openFinder } from "./finder.js";
import { openProblem } from "./problem.js";
import { showListing } from "./listing.js";
import { RATINGS, RATING_NAMES, rate, select } from "./rail.js";
import { esc } from "./render.js";
import { openTarget } from "./review.js";
import { api, state } from "./state.js";

const HELP_SEEN = "oracle-review:help-seen";

document.getElementById("help-button").addEventListener("click", showHelp);
document.addEventListener("keydown", onKey);
document.addEventListener("mouseover", showTip);
window.addEventListener("hashchange", route);
route();

async function route() {
  const app = document.getElementById("app");
  const hash = location.hash.slice(1) || "/";
  try {
    if (!state.listing) {
      state.listing = await api("/api/targets");
      document.getElementById("rater").textContent = `Rating as ${state.listing.rater}`;
    }
    if (hash.startsWith("/problem/")) {
      await openProblem(decodeURIComponent(hash.slice("/problem/".length)));
    } else if (hash.startsWith("/review/")) {
      await openTarget(decodeURIComponent(hash.slice("/review/".length)));
    } else {
      showListing();
      if (!helpSeen()) showHelp();
    }
  } catch (error) {
    app.innerHTML = `<p class="message error">${esc(error.message)}</p>`;
  }
}

function onKey(event) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p" && state.target) {
    event.preventDefault();
    openFinder();
    return;
  }
  if (document.querySelector("dialog[open]")) return;
  const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
  if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
  // TODO: on the trace tab, onTraceKey gets the key first.
  if (event.key === "?") {
    showHelp();
  } else if (state.target && ["1", "2", "3"].includes(event.key)) {
    event.preventDefault();
    rate(state.selected, RATINGS[Number(event.key) - 1], { advance: true });
  } else if (state.target && event.key === "j") {
    select(state.selected + 1);
  } else if (state.target && event.key === "k") {
    select(state.selected - 1);
  }
}

// One floating description for whatever element under the cursor has `data-tip`.
function showTip(event) {
  throw new Error("not implemented");
}

function showHelp() {
  const definitions = state.listing?.rating_definitions;
  document.getElementById("help-ratings").innerHTML = definitions
    ? RATINGS.map((key) => `<dt class="${key}">${RATING_NAMES[key]}</dt><dd>${esc(definitions[key])}</dd>`).join("")
    : "";
  const dialog = document.getElementById("help");
  if (!dialog.open) dialog.showModal();
  try {
    localStorage.setItem(HELP_SEEN, "1");
  } catch {
    // The dialog shows again next time.
  }
}

function helpSeen() {
  try {
    return Boolean(localStorage.getItem(HELP_SEEN));
  } catch {
    return false;
  }
}
