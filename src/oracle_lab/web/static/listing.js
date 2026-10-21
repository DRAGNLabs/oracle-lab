// The home page: every problem and its tasks in rating order, with this rater's
// progress, filters, and Continue. `nextStop` decides where Continue and Next task go.

import { STAGE_NAMES, esc } from "./render.js";
import { state } from "./state.js";

// Where to go after `afterId` (or from the start): the next unrated task's rating
// page, or its problem's page when that task starts a new problem. Null when done.
export function nextStop(afterId = null) {
  throw new Error("not implemented");
}

export function showListing() {
  // TODO: problems, overall and per-problem progress, filters from the hash, Continue.
  state.target = null;
  document.title = "Oracle Lab review";
  const { targets, unreviewable } = state.listing;
  const done = targets.filter((target) => target.reviewed).length;
  const percent = targets.length ? (100 * done) / targets.length : 0;

  document.getElementById("app").innerHTML = `
    <div class="listing">
      <h1>Targets to review</h1>
      <p class="progress-line">${done} of ${targets.length} reviewed</p>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${targets.length}"
        aria-valuenow="${done}"><span style="width: ${percent}%"></span></div>
      ${targets.length ? byTask(targets) : `<p class="message">There is nothing to review in this directory.</p>`}
      ${unreviewable.length ? unreviewableSection(unreviewable) : ""}
    </div>`;
}

function byTask(targets) {
  const tasks = Map.groupBy(targets, (target) => target.instance_id);
  return [...tasks].map(([task, items]) => `
    <section class="task">
      <h2>${esc(task)}</h2>
      <ul class="targets">${items.map(targetRow).join("")}</ul>
    </section>`).join("");
}

function targetRow(target) {
  return `
    <li><a class="target-row ${target.reviewed ? "done" : ""}" href="#/review/${encodeURIComponent(target.id)}">
      <span>${STAGE_NAMES[target.stage]}</span>
      <span class="path">${esc(target.id)}</span>
      <span class="status">${target.reviewed ? "Reviewed" : "Not reviewed"}</span>
    </a></li>`;
}

function unreviewableSection(items) {
  return `
    <section class="unreviewable">
      <h2>Can't be reviewed</h2>
      <ul class="targets">${items.map((item) => `
        <li><span class="path">${esc(item.id)}</span><span class="reason">${esc(item.reason)}</span></li>`).join("")}
      </ul>
    </section>`;
}
