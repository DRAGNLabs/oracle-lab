// Each generated test file's versions as diffs, with the pytest runs after each one.
// Final outcomes appear only when the outcome toggle is on.

import { diffHtml, editFacts, esc } from "./html.js";

export function showTests(container, trace, { showOutcomes, showEditFacts, onSelectStep }) {
  if (!trace.generated_files.length) {
    container.innerHTML = `<p class="muted">No files under the generated test directory.</p>`;
    return;
  }
  container.innerHTML = trace.generated_files
    .map((history) => file(history, showOutcomes, showEditFacts))
    .join("");
  for (const link of container.querySelectorAll("[data-step]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      onSelectStep(Number(link.dataset.step));
    });
  }
}

function file(history, showOutcomes, showEditFacts) {
  const submission = history.matches_submission === null ? ""
    : history.matches_submission ? `<span class="tag">matches submission</span>`
    : `<span class="tag warn">differs from submission</span>`;
  const versions = history.versions.length
    ? history.versions.map((version, index) => versionHtml(version, index, showOutcomes, showEditFacts)).join("")
    : `<p class="muted small">Submitted, but never written by a tool call the viewer can see.</p>`;
  const absent = showEditFacts && history.failed_then_absent.length
    ? `<div class="fact-box">Failed in a run, not defined in the submitted file:${testList(history.failed_then_absent)}</div>`
    : "";
  return `<details class="test-file" open><summary><code>${esc(history.path)}</code> ${submission}</summary>${absent}${versions}</details>`;
}

function testList(ids) {
  return `<ul>${ids.map((id) => `<li><code>${esc(id.split("::").slice(1).join("::"))}</code></li>`).join("")}</ul>`;
}

function versionHtml(version, index, showOutcomes, showEditFacts) {
  const partial = version.partial ? ` <span class="tag warn" title="Content after this change could not be rebuilt; only the change is shown">partial</span>` : "";
  const runs = version.runs_after
    .map(([step, summary]) => {
      const counts = Object.entries(summary.counts).map(([word, count]) => `${count} ${word}`).join(", ") || "no tests ran";
      const failed = summary.failed.length ? `<div class="failed">${summary.failed.map((id) => `FAILED <code>${esc(id)}</code>`).join("<br>")}</div>` : "";
      return `<li><a href="#" data-step="${step}">step ${step}</a>: ${esc(counts)}${failed}</li>`;
    })
    .join("");
  const outcomes = showOutcomes && Object.keys(version.outcomes).length
    ? `<ul class="outcomes">${Object.entries(version.outcomes)
        .map(([id, cell]) => `<li><span class="outcome outcome-${esc(cell)}">${esc(cell.replaceAll("_", " "))}</span> <code>${esc(id.split("::").slice(1).join("::"))}</code></li>`)
        .join("")}</ul>`
    : "";
  const labels = showEditFacts ? editFacts(version).map((label) => `<span class="tag fact">${esc(label)}</span>`).join("") : "";
  const factDetail = showEditFacts && (version.follows_failures.length || version.skip_marks_added.length)
    ? `<div class="fact-box">${version.follows_failures.length ? `Reported as failing before this edit:${testList(version.follows_failures)}` : ""}${
        version.skip_marks_added.length ? `Added lines:<pre>${esc(version.skip_marks_added.join("\n"))}</pre>` : ""
      }</div>`
    : "";
  return `
    <div class="version">
      <div class="version-head">v${index + 1} · <a href="#" data-step="${version.step}">step ${version.step}</a>${partial}${labels}</div>
      ${factDetail}
      <details${index === 0 ? "" : " open"}><summary class="small muted">diff</summary><pre class="diff">${diffHtml(version.diff)}</pre></details>
      ${runs ? `<ul class="runs">${runs}</ul>` : `<p class="small muted">No pytest runs between this version and the next.</p>`}
      ${outcomes}
    </div>`;
}
