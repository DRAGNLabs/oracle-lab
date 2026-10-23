// A judgment's Overview tab: the run, its outcomes, the agent's coverage of the patched
// lines, the edit facts, and the generated tests. The problem itself is the Problem tab.

import { labels, testName } from "./labels.js";
import { esc, fileHtml, fileLink, markdown, testLink } from "./render.js";
import { api, state } from "./state.js";
import { loadTrace } from "./trace.js";

export function renderOverview(panel) {
  const target = state.target;
  const sections = [
    forbiddenChanges(target.submission),
    runHeader(target),
    section("Test outcomes", matrixTable(target.outcomes.matrix) + outcomeTable(target.outcomes)),
    section("Patch coverage", `<div id="coverage"><p class="muted">Loading the agent trace</p></div>`),
    section("Edit facts", `<div id="edit-facts"><p class="muted">Loading the agent trace</p></div>`),
    section("Generated tests", generatedTests(target.submission.files)),
  ];
  panel.innerHTML = `<div class="overview doc">${sections.join("")}</div>`;
  loadGeneratedTests(panel);
  loadTraceFacts(panel);
}

// What this run was: the agent, the localized target, and the prompt it was given.
function runHeader(target) {
  const { harness, model, scope } = target.run;
  const targetText = target.test_target
    ? `<div class="prose target">${markdown(target.test_target)}</div>`
    : "None: the agent was asked to test the whole issue";
  const rows = [
    ["Agent", `${esc(harness)} · ${esc(model)}`],
    ["Scope", esc(scope)],
    ["Localized target", targetText],
  ];
  const prompt = target.prompt
    ? `<details><summary>Prompt given to the agent</summary><pre class="wrap">${esc(target.prompt)}</pre></details>`
    : `<p class="muted">No prompt was saved with this run.</p>`;
  return `
    <section>
      <h2>Run</h2>
      <dl class="meta">${rows.map(([term, value]) => `<dt>${term}</dt><dd>${value}</dd>`).join("")}</dl>
      ${prompt}
    </section>`;
}

// The sections built from the run's trace, filled in once it has loaded.
async function loadTraceFacts(panel) {
  const coverage = panel.querySelector("#coverage");
  const facts = panel.querySelector("#edit-facts");
  try {
    const trace = await loadTrace();
    coverage.innerHTML = patchCoverage(trace.defect);
    facts.innerHTML = editFacts(trace);
  } catch (error) {
    coverage.innerHTML = facts.innerHTML = `<p class="message error">${esc(error.message)}</p>`;
  }
}

// Which lines the golden patch changes, and how many of them the agent saw.
function patchCoverage(defect) {
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
    <table class="doc-table">
      <thead><tr><th>Patched file</th><th>Changed lines (buggy version)</th></tr></thead>
      <tbody>${files}</tbody>
    </table>
    ${names}
    <p>The agent saw <b>${defect.lines_shown} of ${defect.lines_total}</b> changed lines in the output of its reads and searches.${target}
      <span class="muted small" data-tip="Counts changed lines whose text appeared in read or search output. Output the harness cut short does not count.">(a lower bound)</span></p>`;
}

// Facts about the order of test edits and pytest runs, each linked to its step.
function editFacts(trace) {
  const rows = [];
  for (const file of trace.generated_files) {
    const name = file.path.split("/").pop();
    for (const version of file.versions) {
      const link = `<button type="button" class="link-like" data-open-step="${version.step}">${version.step}</button>`;
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
  if (!rows.length) return `<p class="muted">None. No edit followed a failing run, added a skip mark, or removed a failing test.</p>`;
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

function section(title, body) {
  return `<section><h2>${esc(title)}</h2>${body}</section>`;
}

function forbiddenChanges(submission) {
  if (submission.compliant && !submission.forbidden_changes.length) return "";
  return `
    <div class="banner" role="note">
      The agent changed files outside the generated test directory, so this result is
      diagnostic only.
      <ul>${submission.forbidden_changes.map((path) => `<li>${esc(path)}</li>`).join("")}</ul>
    </div>`;
}

// The paired-outcome counts, as the 2×2 grid they are: buggy result by golden result.
function matrixTable(matrix) {
  const cell = (key, expected = false) => {
    const count = matrix[key] ?? 0;
    return `<td class="${expected ? "expected" : ""} ${count ? "" : "zero"}" title="${labels().cells[key]}">${count}</td>`;
  };
  return `
    <table class="matrix" aria-label="Paired outcomes: buggy result by golden result">
      <tr><td></td><th scope="col">Golden passes</th><th scope="col">Golden fails</th></tr>
      <tr><th scope="row">Buggy fails</th>${cell("fail_on_buggy_pass_on_golden", true)}${cell("fail_on_both")}</tr>
      <tr><th scope="row">Buggy passes</th>${cell("pass_on_both")}${cell("pass_on_buggy_fail_on_golden")}</tr>
    </table>`;
}

function outcomeTable({ tests, other_outcomes: other }) {
  const rows = [...tests, ...other.filter((o) => !tests.some((t) => t.test_id === o.test_id))];
  if (!rows.length) return `<p>No generated test produced an outcome.</p>`;
  const result = (value) => `<span class="${value === "pass" || value === "fail" ? value : ""}">${esc(value)}</span>`;
  const failure = (version, value) => {
    if (!value) return "";
    const message = [value.exception_type, value.message].filter(Boolean).join(": ");
    return `
      <details><summary>${version} failure</summary>
        <pre class="failure">${esc(message || JSON.stringify(value, null, 2))}</pre>
      </details>`;
  };
  return `
    <table class="outcomes">
      <thead><tr><th>Generated test</th><th>Buggy</th><th>Golden</th><th>Outcome</th></tr></thead>
      <tbody>${rows.map((row) => `
        <tr>
          <td class="test">
            ${testLink(row.test_id)}
            ${failure("Buggy", row.buggy_failure)}${failure("Golden", row.golden_failure)}
          </td>
          <td>${result(row.buggy)}</td>
          <td>${result(row.golden)}</td>
          <td>${row.cell ? labels().cells[row.cell] : "Incomplete"}</td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

// Each generated test file's heading now; its code once fetched.
function generatedTests(paths) {
  if (!paths.length) return `<p>The agent captured no test files.</p>`;
  return paths.map((path) => `
    <h3>${fileLink(path, "buggy", esc(path))}</h3>
    <div class="generated" data-path="${esc(path)}"><p class="message">Loading</p></div>`).join("");
}

async function loadGeneratedTests(panel) {
  for (const holder of panel.querySelectorAll(".generated")) {
    const path = holder.dataset.path;
    try {
      holder.innerHTML = fileHtml(await api("/api/file", { id: state.target.id, view: "buggy", path }), path);
    } catch (error) {
      holder.innerHTML = `<p class="message error">${esc(error.message)}</p>`;
    }
  }
}
