// The run's context, read first: the task instance, the issue, what the agent was
// told, the fix and reference tests, the final results, and the edit facts.

import { DESCRIPTIONS, diffHtml, esc, markdown, testName } from "./html.js";

export function showSummary(container, trace, { onSelectStep }) {
  const task = trace.task;
  container.innerHTML = `<div class="doc">${[
    header(trace),
    section("Issue", task.issue ? `<article class="prose">${markdown(withoutTitle(task.issue, task.instance_id))}</article>` : missing("No issue text was saved with this run.")),
    section("Prompt given to the agent", task.prompt
      ? `<details><summary>Show the prompt</summary><pre class="wrap">${esc(task.prompt)}</pre></details>`
      : missing("No prompt was saved with this run.")),
    section("Golden patch", patch(trace)),
    section("Reference tests", referenceTests(task)),
    section("Results", results(task.results)),
    section("Edit facts", editFacts(trace)),
  ].join("")}</div>`;
  for (const link of container.querySelectorAll("[data-step]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      onSelectStep(Number(link.dataset.step));
    });
  }
}

// Saved issues start with the instance id as a heading, which the header already shows.
function withoutTitle(issue, instanceId) {
  const [first, ...rest] = issue.split("\n");
  return first.replace(/^#\s+/, "").trim() === instanceId ? rest.join("\n") : issue;
}

function section(title, body) {
  return `<section><h2>${esc(title)}</h2>${body}</section>`;
}

function missing(text) {
  return `<p class="muted">${esc(text)}</p>`;
}

function header(trace) {
  const { task, run } = trace;
  const scope = task.test_target
    ? `<div class="prose target">${markdown(task.test_target)}</div>`
    : "None: the agent was asked to test the whole issue";
  const rows = [
    ["Repository", esc(task.repo)],
    ["Dataset", esc(task.dataset)],
    ["Base commit", `<code>${esc(task.base_commit.slice(0, 12))}</code>`],
    ["Agent", `${esc(run.harness)} · ${esc(run.model)}`],
    ["Localized target", scope],
  ];
  return `
    <header class="doc-head">
      <h1>${esc(task.instance_id)}</h1>
      <dl class="meta">${rows.map(([term, value]) => `<dt>${term}</dt><dd>${value}</dd>`).join("")}</dl>
    </header>`;
}

function patch(trace) {
  const { task, defect } = trace;
  if (!task.golden_patch) return missing("No golden patch was saved with this run.");
  const ranges = (hunks) => hunks.map(([start, end]) => (start === end ? `${start}` : `${start}–${end}`)).join(", ");
  const files = Object.entries(defect.hunks)
    .map(([path, hunks]) => `<tr><td><code>${esc(path)}</code></td><td>${ranges(hunks)}</td></tr>`)
    .join("");
  const names = defect.names.length
    ? `<p>It changes ${defect.names.map((name) => `<code>${esc(name)}</code>`).join(", ")}.</p>`
    : "";
  const target = defect.target_read === null ? ""
    : ` The agent ${defect.target_read ? "was" : "was not"} shown the definition of the localized target.`;
  return `
    <p>The fix for the issue. The agent never sees it.</p>
    <table class="doc-table">
      <thead><tr><th>File</th><th>Changed lines (buggy version)</th></tr></thead>
      <tbody>${files}</tbody>
    </table>
    ${names}
    <p>The agent saw <b>${defect.lines_shown} of ${defect.lines_total}</b> changed lines in the output of its reads and searches.${target}
      <span class="muted small" data-tip="Counts changed lines whose text appeared in read or search output. Output the harness cut short does not count.">(a lower bound)</span></p>
    <details open><summary>Diff</summary><pre class="diff">${diffHtml(task.golden_patch)}</pre></details>`;
}

function referenceTests(task) {
  const ids = task.reference_test_ids.length
    ? `<p>The dataset's own tests for this fix, which the agent never sees:</p><ul class="ids">${task.reference_test_ids.map((id) => `<li><code>${esc(id)}</code></li>`).join("")}</ul>`
    : missing("No reference test IDs.");
  const diff = task.reference_test_patch
    ? `<details><summary>Reference test patch</summary><pre class="diff">${diffHtml(task.reference_test_patch)}</pre></details>`
    : "";
  return ids + diff;
}

function results(rows) {
  if (rows === null) return missing("This run was not evaluated.");
  if (!rows.length) return missing("No generated test produced an outcome.");
  const count = (cell) => rows.filter((row) => row.cell === cell).length;
  const cell = (key, expected = false) =>
    `<td class="${expected ? "expected" : ""} ${count(key) ? "" : "zero"}" data-tip="${esc(DESCRIPTIONS.outcome[key])}">${count(key)}</td>`;
  const matrix = `
    <p>Each generated test, run on the buggy code and on the fixed code. Detecting the bug means failing on buggy and passing on golden.</p>
    <table class="matrix">
      <tr><td></td><th scope="col">Golden passes</th><th scope="col">Golden fails</th></tr>
      <tr><th scope="row">Buggy fails</th>${cell("fail_on_buggy_pass_on_golden", true)}${cell("fail_on_both")}</tr>
      <tr><th scope="row">Buggy passes</th>${cell("pass_on_both")}${cell("pass_on_buggy_fail_on_golden")}</tr>
    </table>`;
  const result = (value) => `<span class="result-${esc(value)}">${esc(value)}</span>`;
  const failure = (label, text) => (text ? `<details><summary>${label} failure</summary><pre class="wrap">${esc(text)}</pre></details>` : "");
  const sorted = [...rows].sort((a, b) => (a.cell === "fail_on_buggy_pass_on_golden" ? -1 : 0) - (b.cell === "fail_on_buggy_pass_on_golden" ? -1 : 0));
  const table = `
    <table class="doc-table results">
      <thead><tr><th>Generated test</th><th>Buggy</th><th>Golden</th></tr></thead>
      <tbody>${sorted.map((row) => `
        <tr class="${row.cell === "fail_on_buggy_pass_on_golden" ? "detects" : ""}">
          <td><code>${esc(testName(row.test_id))}</code>${failure("Buggy", row.buggy_failure)}${failure("Golden", row.golden_failure)}</td>
          <td>${result(row.buggy)}</td>
          <td>${result(row.golden)}</td>
        </tr>`).join("")}
      </tbody>
    </table>`;
  return matrix + table;
}

function editFacts(trace) {
  const rows = [];
  for (const file of trace.generated_files) {
    const name = file.path.split("/").pop();
    for (const version of file.versions) {
      const link = `<a href="#" data-step="${version.step}">${version.step}</a>`;
      if (version.follows_failures.length) {
        rows.push([link, name, `Edited after a pytest run reported ${version.follows_failures.length} failing test${version.follows_failures.length > 1 ? "s" : ""} in this file: ${testIds(version.follows_failures)}`]);
      } else if (version.follows_failed_run) {
        rows.push([link, name, "Edited after a pytest run reported failures elsewhere."]);
      }
      if (version.skip_marks_added.length) {
        rows.push([link, name, `Added a skip or xfail mark: <code>${esc(version.skip_marks_added[0].trim())}</code>`]);
      }
    }
    for (const id of file.failed_then_absent) {
      rows.push(["", name, `Reported as failing, and not defined in the submitted file: ${testIds([id])}`]);
    }
  }
  if (!rows.length) return missing("None. No edit followed a failing run, added a skip mark, or removed a failing test.");
  return `
    <p>Facts about the order of test edits and pytest runs. They say what happened, not whether it was a mistake.</p>
    <table class="doc-table">
      <thead><tr><th>Step</th><th>File</th><th>Fact</th></tr></thead>
      <tbody>${rows.map((row) => `<tr>${row.map((value) => `<td>${value}</td>`).join("")}</tr>`).join("")}</tbody>
    </table>`;
}

function testIds(ids) {
  return ids.map((id) => `<code>${esc(testName(id))}</code>`).join(", ");
}
