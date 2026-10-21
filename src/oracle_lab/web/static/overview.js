// A judgment's Overview tab: the outcomes, the agent's coverage of the patched lines,
// the edit facts, and the generated tests. The problem itself is the Problem tab.

import { diffHtml, esc, fileHtml, fileLink, markdown, parseDiff, testLink } from "./render.js";
import { api, state } from "./state.js";

const CELLS = {
  fail_on_buggy_pass_on_golden: "Fails on buggy, passes on golden",
  pass_on_both: "Passes on both",
  fail_on_both: "Fails on both",
  pass_on_buggy_fail_on_golden: "Passes on buggy, fails on golden",
};

export function renderOverview(panel) {
  const target = state.target;
  const judgment = target.stage === "judge";
  const sections = [];
  if (judgment) sections.push(forbiddenChanges(target.submission));
  sections.push(section("Issue", `<article class="prose">${markdown(target.issue)}</article>`));
  if (judgment) {
    sections.push(section("Test outcomes", matrixTable(target.outcomes.matrix) + outcomeTable(target.outcomes)));
    sections.push(section("Generated tests", generatedTests(target.submission.files)));
  } else {
    for (const { title, text } of target.record_texts) {
      sections.push(section(title, `<article class="prose">${markdown(text)}</article>`));
    }
  }
  sections.push(section("Repair", diffHtml(state.repair, target.views)));
  sections.push(section("Reference tests", referenceTests(target)));

  panel.innerHTML = `<div class="overview">${sections.join("")}</div>`;
  if (judgment) loadGeneratedTests(panel);
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
    return `<td class="${expected ? "expected" : ""} ${count ? "" : "zero"}" title="${CELLS[key]}">${count}</td>`;
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
          <td>${row.cell ? CELLS[row.cell] : "Incomplete"}</td>
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

function referenceTests(target) {
  const ids = target.reference_test_ids.length
    ? `<h3>Reference test IDs</h3><ul>${target.reference_test_ids.map((id) => `<li><code>${esc(id)}</code></li>`).join("")}</ul>`
    : "";
  return ids + diffHtml(parseDiff(target.reference_test_patch), target.views);
}
