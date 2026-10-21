// Each test file the agent wrote under the generated directory, as the sequence of
// versions its writes and edits produced, with the pytest runs after each one.

import { DESCRIPTIONS, counts, diffHtml, editFacts, esc, outcomeTag, tag, testName } from "./html.js";

const INTRO = `
  <p class="intro">Every file the agent wrote under the generated test directory. Each
  write or edit to a file makes a new version. A version shows its diff against the
  previous version (the first shows the whole file), the pytest runs between it and
  the next version, and the final evaluation result of each test it added.</p>`;

export function showTests(container, trace, { onSelectStep }) {
  if (!trace.generated_files.length) {
    container.innerHTML = `${INTRO}<p class="muted">No files under the generated test directory.</p>`;
    return;
  }
  // The edit that made each version, by "step:part", for the version headers.
  const edits = new Map(trace.events.map((event) => [`${event.step}:${event.part}`, event]));
  container.innerHTML = INTRO + overview(trace.generated_files) + trace.generated_files.map((history) => file(history, edits)).join("");
  for (const link of container.querySelectorAll("[data-step]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      onSelectStep(Number(link.dataset.step));
    });
  }
}

// One line per file, so the files and their version counts are visible before the details.
function overview(files) {
  const rows = files
    .map((history) => `<tr><td><code>${esc(history.path)}</code></td><td>${history.versions.length}</td><td>${submissionTag(history)}</td></tr>`)
    .join("");
  return `<table class="doc-table"><thead><tr><th>File</th><th>Versions</th><th>Final version</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function submissionTag(history) {
  return history.matches_submission === null ? `<span class="muted small" data-tip="An edit could not be followed, so the viewer does not know the final content.">unknown</span>`
    : history.matches_submission ? tag("matches submission", DESCRIPTIONS.file.matches_submission)
    : tag("differs from submission", DESCRIPTIONS.file.differs_submission, "warn");
}

function file(history, edits) {
  const versions = history.versions.length
    ? history.versions.map((version, index) => versionHtml(history, version, index, edits)).join("")
    : `<p class="muted small">Submitted, but never written by a tool call the viewer can see.</p>`;
  const absent = history.failed_then_absent.length
    ? `<div class="fact-box" data-tip="${esc(DESCRIPTIONS.fact.failed_then_absent)}">Failed in a run, not defined in the submitted file:${testList(history.failed_then_absent)}</div>`
    : "";
  return `<details class="test-file" open><summary><code>${esc(history.path)}</code> ${submissionTag(history)}</summary>${absent}${versions}</details>`;
}

// The diff against the previous version, or, when the viewer could not rebuild the
// file, why not and what it does know.
function change(version, index, edit) {
  if (version.unknown === "shell") {
    return `<div class="unknown-box">
      <p>A shell command changed this file. The trace records the command, not its result,
      so the file's content is unknown from this version on.</p>
      ${edit?.command ? `<pre class="wrap">${esc(edit.command)}</pre>` : ""}
    </div>`;
  }
  const diff = `<pre class="diff">${diffHtml(version.diff)}</pre>`;
  if (version.unknown) {
    const why = version.unknown === "no_base"
      ? "The file's content before this edit is unknown, because an earlier version could not be rebuilt."
      : "The text this edit replaced is not in the previous version as the viewer rebuilt it.";
    return `<div class="unknown-box"><p>${why} Below is only the text the edit replaced and its replacement, not a diff of the whole file.</p></div>
      <details open><summary class="small muted">Replaced text</summary>${diff}</details>`;
  }
  return `<details${index === 0 ? "" : " open"}><summary class="small muted">${index === 0 ? "Whole file" : `Diff from version ${index}`}</summary>${diff}</details>`;
}

function testList(ids) {
  return `<ul>${ids.map((id) => `<li><code>${esc(testName(id))}</code></li>`).join("")}</ul>`;
}

function versionHtml(history, version, index, edits) {
  const edit = edits.get(`${version.step}:${version.part}`);
  // A chained command can run pytest more than once in one step; number those runs.
  const perStep = new Map();
  for (const [step] of version.runs_after) perStep.set(step, (perStep.get(step) || 0) + 1);
  const seen = new Map();
  const runs = version.runs_after
    .map(([step, summary]) => {
      seen.set(step, (seen.get(step) || 0) + 1);
      const which = perStep.get(step) > 1 ? ` (run ${seen.get(step)} of ${perStep.get(step)})` : "";
      const failed = summary.failed.length ? `<ul class="failed">${summary.failed.map((id) => `<li>FAILED <code>${esc(testName(id))}</code></li>`).join("")}</ul>` : "";
      return `<li><a href="#" data-step="${step}">step ${step}</a>${which}: ${esc(counts(summary))}${failed}</li>`;
    })
    .join("");
  const added = Object.entries(version.outcomes);
  const detecting = added.filter(([, cell]) => cell === "fail_on_buggy_pass_on_golden").length;
  const outcomes = added.length
    ? `<details class="version-outcomes"><summary class="small">Final results of the ${added.length} test${added.length > 1 ? "s" : ""} added in this version: ${detecting} detect${detecting === 1 ? "s" : ""} the bug</summary><ul class="outcomes">${Object.entries(version.outcomes)
        .map(([id, cell]) => `<li>${outcomeTag(cell)} <code>${esc(testName(id))}</code></li>`)
        .join("")}</ul></details>`
    : "";
  const factDetail = version.follows_failures.length || version.skip_marks_added.length
    ? `<div class="fact-box">${version.follows_failures.length ? `Reported as failing before this edit:${testList(version.follows_failures)}` : ""}${
        version.skip_marks_added.length ? `Added lines:<pre>${esc(version.skip_marks_added.join("\n"))}</pre>` : ""
      }</div>`
    : "";
  return `
    <div class="version">
      <div class="version-head">
        <span>Version ${index + 1} of ${history.versions.length}</span>
        <span class="muted">·</span>
        <a href="#" data-step="${version.step}">step ${version.step}</a>
      </div>
      <div class="version-edit">${edit ? esc(edit.description) : `<code>${esc(history.path)}</code>`}</div>
      ${editFacts(version).length ? `<div class="version-tags">${editFacts(version).join("")}</div>` : ""}
      ${factDetail}
      ${change(version, index, edit)}
      <h5>Pytest runs before the next version</h5>
      ${runs ? `<ul class="runs">${runs}</ul>` : `<p class="small muted none">None.</p>`}
      ${outcomes}
    </div>`;
}
