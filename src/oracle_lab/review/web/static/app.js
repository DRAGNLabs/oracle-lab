// The review page: one rater rates one LLM output at a time against its evidence.
// This entry point routes between the home page and a rating page, and owns the
// keyboard, the hover text, and the help dialog. Plain JavaScript
// modules, no build step.

import { openFinder } from "./finder.js";
import { showListing } from "./listing.js";
import { RATINGS, rate, select } from "./rail.js";
import { esc, markdown } from "./render.js";
import { openTarget } from "./review.js";
import { api, state } from "./state.js";
import { onTraceKey } from "./trace.js";

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
    const rating = hash.startsWith("/review/");
    document.getElementById("help-button").hidden = !rating;
    if (rating) {
      await openTarget(decodeURIComponent(hash.slice("/review/".length)));
    } else {
      showListing();
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
  if (state.target && onTraceKey(event)) {
    event.preventDefault();
  } else if (state.target && event.key === "?") {
    showHelp();
  } else if (state.target && ["1", "2"].includes(event.key)) {
    event.preventDefault();
    rate(state.selected, RATINGS[Number(event.key) - 1], { advance: true });
  } else if (state.target && event.key === "j") {
    select(state.selected + 1);
  } else if (state.target && event.key === "k") {
    select(state.selected - 1);
  }
}

// One floating description for whatever element under the cursor has `data-tip`.
// The rating buttons show their own.
const tooltip = document.getElementById("tooltip");
function showTip(event) {
  const target = event.target.closest?.("[data-tip]");
  if (!target || target.closest(".rate")) {
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

// The reviewer's instructions, from `review/web/templates/how-to-review.md`.
function showHelp() {
  document.getElementById("help-body").innerHTML = markdown(state.listing?.how_to_review || "");
  const dialog = document.getElementById("help");
  if (!dialog.open) dialog.showModal();
}
