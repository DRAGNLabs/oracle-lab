// The sidebar's Legend tab: what every color, tag, and fact in the trace means, from
// the shared descriptions.

import { ACTION_LABELS, CATEGORY_LABELS, DESCRIPTIONS } from "./labels.js";
import { esc } from "./render.js";

const TAG_LABELS = { patched_name: "function or class name, in orange", git_history: "git history", exit: "exit N" };
const FACT_LABELS = {
  after_failures: "after failures in this file (N)",
  after_failed_run: "after a run with failures",
  adds_skip: "adds skip/xfail",
  failed_then_absent: "failed in a run, not in the submitted file",
};
const FILE_LABELS = { matches_submission: "matches submission", differs_submission: "differs from submission" };

export function showLegend(container) {
  const actions = Object.entries(ACTION_LABELS)
    .map(([key, label]) => row(`<span class="dot" style="--dot: var(--a-${key})"></span>${esc(label)}`, DESCRIPTIONS.action[key]))
    .join("");
  const overlap = row(`<span class="swatch overlap-lines"></span>patched lines`, DESCRIPTIONS.overlap.lines) +
    row(`<span class="swatch overlap-file"></span>patched file`, DESCRIPTIONS.overlap.file);
  const tags = Object.entries(DESCRIPTIONS.tag)
    .map(([key, description]) => row(esc(TAG_LABELS[key] || CATEGORY_LABELS[key]), description))
    .join("");
  const named = (labels, descriptions) => Object.entries(descriptions).map(([key, description]) => row(esc(labels[key] || key.replaceAll("_", " ")), description)).join("");
  container.innerHTML = `
    <h4>Action types</h4><p class="muted small">The colored dots and lanes in the timeline.</p><dl>${actions}</dl>
    <h4>Patch overlap</h4><p class="muted small">The bar at the left of a row. The golden patch is the fix for this task's bug.</p><dl>${overlap}</dl>
    <h4>Row tags</h4><dl>${tags}</dl>
    <h4>Edit facts</h4><p class="muted small">Facts about the order of test edits and runs. They describe what happened, not whether it was a mistake.</p><dl>${named(FACT_LABELS, DESCRIPTIONS.fact)}</dl>
    <h4>Test file tags</h4><dl>${named(FILE_LABELS, DESCRIPTIONS.file)}</dl>
    <h4>Final results</h4><p class="muted small">Each test's result on the buggy and fixed code. Other outcomes, such as errors or skips, show as "buggy / golden".</p><dl>${named({}, DESCRIPTIONS.outcome)}</dl>
    <h4>Keys</h4><p>On the Agent trace tab, <kbd>↑</kbd>/<kbd>↓</kbd> move between events, and <kbd>Esc</kbd> clears the selection.</p>`;
}

function row(term, description) {
  return `<dt>${term}</dt><dd>${esc(description)}</dd>`;
}
