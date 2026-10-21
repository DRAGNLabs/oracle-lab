// The Problem tab of every rating page: what a rater reads to understand the task
// before rating anything about it.

import { diffHtml, esc, markdown, parseDiff } from "./render.js";

// The problem view into `container`: the instance, the issue, the record's other
// texts, the golden patch, and the reference tests. File names in the patches open
// the rating page's repository `views`.
export function renderProblem(container, problem, views) {
  container.innerHTML = `<div class="doc">${[
    header(problem),
    section("Issue", `<article class="prose">${markdown(withoutTitle(problem.issue, problem.instance_id))}</article>`),
    ...problem.record_texts.map(({ title, text }) => section(title, `<article class="prose">${markdown(text)}</article>`)),
    section("Golden patch", `<p>The fix for the issue. The agent never sees it.</p>${diffHtml(parseDiff(problem.repair_patch), views)}`),
    section("Reference tests", referenceTests(problem, views)),
  ].join("")}</div>`;
}

function section(title, body) {
  return `<section><h2>${esc(title)}</h2>${body}</section>`;
}

// Saved issues may start with the instance id as a heading, which the header already shows.
function withoutTitle(issue, instanceId) {
  const [first, ...rest] = issue.replaceAll("\r\n", "\n").split("\n");
  return first.replace(/^#\s+/, "").trim() === instanceId ? rest.join("\n") : issue;
}

function header(problem) {
  const rows = [
    ["Repository", esc(problem.repo)],
    ["Dataset", esc(problem.dataset)],
    ["Base commit", `<code>${esc(problem.base_commit.slice(0, 12))}</code>`],
  ];
  return `
    <header class="doc-head">
      <h1>${esc(problem.instance_id)}</h1>
      <dl class="meta">${rows.map(([term, value]) => `<dt>${term}</dt><dd>${value}</dd>`).join("")}</dl>
    </header>`;
}

function referenceTests(problem, views) {
  const ids = problem.reference_test_ids.length
    ? `<p>The dataset's own tests for this fix, which the agent never sees:</p><ul class="ids">${problem.reference_test_ids.map((id) => `<li><code>${esc(id)}</code></li>`).join("")}</ul>`
    : `<p class="muted">No reference test IDs.</p>`;
  return `${ids}<details><summary>Reference test patch</summary>${diffHtml(parseDiff(problem.reference_test_patch), views)}</details>`;
}
