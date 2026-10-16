// One target's review page: the question rail beside tabs of evidence.

import { openEvidence, renderEvidence, startEvidence } from "./evidence.js";
import { openFile, openTest, renderFiles, startBrowser } from "./files.js";
import { renderOverview } from "./overview.js";
import { startRail } from "./rail.js";
import { STAGE_NAMES, esc, markdown, parseDiff } from "./render.js";
import { api, state } from "./state.js";

const app = document.getElementById("app");
const RENDER_TAB = {
  overview: renderOverview,
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

  const tabs = [
    ["overview", "Overview"],
    ["files", "Files"],
    ["evidence", target.stage === "judge" ? "Agent session" : "Task record"],
    ["rubric", "Stage rubric"],
  ];
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
  showTab("overview");
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
  const link = event.target.closest("[data-open-file], [data-open-test], [data-open-evidence]");
  if (!link) return;
  const { openFile: path, openTest: testId, openEvidence: name, view, line } = link.dataset;
  if (testId) openTest(testId);
  else if (path) openFile(path, { view: view || "buggy", line: Number(line) || null });
  else openEvidence(name);
}
