// The home page: every problem and its tasks in rating order, with this rater's
// progress and filters. Each task opens its rating page.

import { draftRatings } from "./rail.js";
import { STAGE_NAMES, esc } from "./render.js";
import { state } from "./state.js";

// The first unreviewed task after `afterId` in rating order, wrapping around. Null when done.
export function nextTask(afterId) {
  const tasks = state.listing.problems.flatMap((problem) => problem.tasks);
  const start = tasks.findIndex((task) => task.id === afterId);
  for (let step = 1; step < tasks.length; step++) {
    const task = tasks[(start + step) % tasks.length];
    if (!task.reviewed) return task;
  }
  return null;
}

// How many of a task's questions this rater has rated, saved or only in the browser.
function ratedCount(task) {
  return task.reviewed ? task.questions.length : Object.keys(draftRatings(task.id, task.questions)).length;
}

// One task as a row that opens its rating page.
function taskRow(task) {
  const rated = ratedCount(task);
  const status = task.reviewed ? "Reviewed" : rated ? `${rated} of ${task.questions.length} rated` : "Not started";
  return `
    <li><a class="target-row ${task.reviewed ? "done" : rated ? "started" : ""}" href="#/review/${encodeURIComponent(task.id)}">
      <span>${STAGE_NAMES[task.stage]}</span>
      <span class="label">${taskLabel(task)}</span>
      <span class="status">${status}</span>
    </a></li>`;
}

// What tells a problem's tasks apart: the agent and scope for a run, the attempt for a classification.
function taskLabel(task) {
  if (!task.run) return `<span class="path">${esc(task.id.split("/").pop())}</span>`;
  const { harness, model, scope } = task.run;
  return `${esc(harness)} · ${esc(model.split("/").pop())} · ${esc(scope)} <span class="path">${esc(task.id.split("/").pop())}</span>`;
}

export function showListing() {
  state.target = null;
  document.title = "Oracle Lab review";
  const { problems, unreviewable } = state.listing;
  const filters = readFilters();
  const tasks = problems.flatMap((problem) => problem.tasks);
  const done = tasks.filter((task) => task.reviewed).length;
  const percent = tasks.length ? (100 * done) / tasks.length : 0;
  const harnesses = [...new Set(tasks.filter((task) => task.run).map((task) => task.run.harness))].sort();

  const shown = problems
    .map((problem) => ({ ...problem, visible: problem.tasks.filter((task) => matches(task, filters)) }))
    .filter((problem) => problem.visible.length);

  document.getElementById("app").innerHTML = `
    <div class="listing">
      <h1>Problems to rate</h1>
      <p class="progress-line">${done} of ${tasks.length} tasks reviewed, in ${problems.length} problem${problems.length === 1 ? "" : "s"}</p>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${tasks.length}"
        aria-valuenow="${done}"><span style="width: ${percent}%"></span></div>
      <form class="filters" id="filters">
        <label>Stage <select name="stage">
          ${option("", "All", filters.stage)}${option("classification", STAGE_NAMES.classification, filters.stage)}${option("judge", STAGE_NAMES.judge, filters.stage)}
        </select></label>
        <label>Harness <select name="harness">
          ${option("", "All", filters.harness)}${harnesses.map((name) => option(name, name, filters.harness)).join("")}
        </select></label>
        <label><input type="checkbox" name="open" ${filters.open ? "checked" : ""}> Not finished only</label>
      </form>
      ${!tasks.length ? `<p class="message">There is nothing to review in this directory.</p>`
        : shown.length ? shown.map(problemSection).join("") : `<p class="message">No task matches these filters.</p>`}
      ${unreviewable.length ? unreviewableSection(unreviewable) : ""}
    </div>`;
  document.getElementById("filters").addEventListener("change", (event) => writeFilters(event.currentTarget));
}

function problemSection(problem) {
  const done = problem.tasks.filter((task) => task.reviewed).length;
  return `
    <section class="task">
      <div class="problem-head ${done === problem.tasks.length ? "done" : ""}">
        <h2>${esc(problem.instance_id)}</h2>
        <span class="issue-title" title="${esc(problem.title)}">${esc(problem.title)}</span>
        <span class="count">${done} of ${problem.tasks.length} done</span>
      </div>
      <ul class="targets">${problem.visible.map(taskRow).join("")}</ul>
    </section>`;
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

function option(value, label, selected) {
  return `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`;
}

// Filters live in the hash, `#/?stage=judge&harness=codex&open=1`, so a reload keeps them.
function readFilters() {
  const query = new URLSearchParams(location.hash.split("?")[1] || "");
  return { stage: query.get("stage") || "", harness: query.get("harness") || "", open: query.get("open") === "1" };
}

function writeFilters(form) {
  const query = new URLSearchParams();
  if (form.stage.value) query.set("stage", form.stage.value);
  if (form.harness.value) query.set("harness", form.harness.value);
  if (form.open.checked) query.set("open", "1");
  location.hash = query.size ? `#/?${query}` : "#/";
}

function matches(task, { stage, harness, open }) {
  return (!stage || task.stage === stage)
    && (!harness || task.run?.harness === harness)
    && (!open || !task.reviewed);
}
