// One target's rating page: the question rail beside tabs of evidence. The Problem
// tab comes first on every page; a judgment adds its run's trace and test files.

import { openEvidence, renderEvidence, startEvidence } from "./evidence.js";
import { openFile, openTest, renderFiles, startBrowser } from "./files.js";
import { renderOverview } from "./overview.js";
import { renderProblem } from "./problem.js";
import { startRail } from "./rail.js";
import { STAGE_NAMES, esc, markdown, parseDiff } from "./render.js";
import { api, state } from "./state.js";
import { showTests } from "./tests.js";
import { openStep, renderTrace } from "./trace.js";

const app = document.getElementById("app");
const RENDER_TAB = {
  problem: (panel) => renderProblem(panel, state.problem),
  overview: renderOverview,
  trace: renderTrace,
  tests: (panel) => {
    throw new Error("not implemented"); // showTests over state.trace, with step links
  },
  files: renderFiles,
  evidence: renderEvidence,
  rubric: (panel) => {
    panel.innerHTML = `<div class="rubric"><article class="prose">${markdown(state.target.stage_rubric)}</article></div>`;
  },
};

export async function openTarget(id) {
  app.innerHTML = `<p class="message">Loading ${esc(id)}</p>`;
  const target = await api("/api/target", { id });
  state.target = target;
  state.repair = parseDiff(target.repair_patch);
  state.trees = {};
  startBrowser(target);
  startEvidence(target);
  document.title = `${target.instance_id} review`;

  // TODO: fetch state.problem with the target.
  const tabs = target.stage === "judge"
    ? [["problem", "Problem"], ["overview", "Overview"], ["trace", "Agent trace"], ["tests", "Test files"],
      ["files", "Files"], ["evidence", "Evidence"], ["rubric", "Stage rubric"]]
    : [["problem", "Problem"], ["files", "Files"], ["evidence", "Task record"], ["rubric", "Stage rubric"]];
  app.innerHTML = `
    <div class="review">
      <aside class="rail" aria-label="Questions to rate">
        <div class="rail-head">
          <a href="#/">All targets</a>
          <h1>${esc(target.instance_id)}</h1>
          <p>${STAGE_NAMES[target.stage]} of ${esc(target.id)}</p>
        </div>
        <ol class="questions" id="questions"></ol>
        <div class="rail-foot" id="rail-foot"></div>
      </aside>
      <section class="main">
        <nav class="tabs" role="tablist">${tabs.map(([key, name]) => `
          <button type="button" role="tab" data-tab="${key}">${name}</button>`).join("")}
        </nav>
        <div class="panel" id="panel" role="tabpanel"></div>
      </section>
    </div>`;

  app.querySelector(".tabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (button) showTab(button.dataset.tab);
  });
  app.querySelector(".review").addEventListener("click", followLink);
  startRail(target);
  showTab("problem");
}

export function showTab(tab) {
  state.tab = tab;
  for (const button of app.querySelectorAll("[data-tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  }
  const panel = document.getElementById("panel");
  panel.scrollTop = 0;
  RENDER_TAB[tab](panel);
}

// Links anywhere on the page open the file, test, or evidence they name.
function followLink(event) {
  // TODO: data-open-step opens the trace tab at that step (openStep).
  const link = event.target.closest("[data-open-file], [data-open-test], [data-open-evidence]");
  if (!link) return;
  const { openFile: path, openTest: testId, openEvidence: name, view, line } = link.dataset;
  if (testId) openTest(testId);
  else if (path) openFile(path, { view: view || "buggy", line: Number(line) || null });
  else openEvidence(name);
}
