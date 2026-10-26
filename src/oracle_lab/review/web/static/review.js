// One target's rating page: the question rail, the middle tabs with what the judge
// rated, and a resizable sidebar with reference: the problem, the stage rubric, and,
// for a judgment, the trace legend and the selected trace event.

import { openEvidence, renderEvidence, startEvidence } from "./evidence.js";
import { openFile, openTest, renderFiles, startBrowser } from "./files.js";
import { showLegend } from "./legend.js";
import { renderOverview } from "./overview.js";
import { renderProblem } from "./problem.js";
import { startRail } from "./rail.js";
import { STAGE_NAMES, esc, markdown, parseDiff } from "./render.js";
import { api, state } from "./state.js";
import { showTests } from "./tests.js";
import { loadTrace, openStep, renderEvent, renderTrace } from "./trace.js";

const app = document.getElementById("app");
const SIDEBAR_WIDTH = "oracle-review:sidebar-width";

// The middle tabs hold what the judge rated for this target.
const RENDER_TAB = {
  overview: renderOverview,
  trace: renderTrace,
  tests: renderTestFiles,
  files: renderFiles,
  evidence: renderEvidence,
};
const MIDDLE_TABS = {
  judge: [["overview", "Overview"], ["trace", "Agent trace"], ["tests", "Test files"], ["files", "Files"], ["evidence", "Evidence"]],
  classification: [["files", "Files"], ["evidence", "Task record"]],
};

// The sidebar tabs hold reference: the problem, the rubric, and the trace's legend and event.
const RENDER_SIDE = {
  problem: (panel) => renderProblem(panel, state.problem, state.target.views),
  rubric: (panel) => {
    panel.innerHTML = `<div class="rubric"><article class="prose">${markdown(state.target.stage_rubric)}</article></div>`;
  },
  legend: (panel) => {
    panel.innerHTML = `<div class="side-doc trace-ui"></div>`;
    showLegend(panel.firstElementChild);
  },
  event: renderEvent,
};
const SIDE_TABS = {
  judge: [["problem", "Problem"], ["rubric", "Stage rubric"], ["legend", "Legend"], ["event", "Event"]],
  classification: [["problem", "Problem"], ["rubric", "Stage rubric"]],
};

export async function openTarget(id) {
  app.innerHTML = `<p class="message">Loading ${esc(id)}</p>`;
  const target = await api("/api/target", { id });
  if (state.problem?.instance_id !== target.instance_id) {
    state.problem = await api("/api/problem", { instance: target.instance_id });
  }
  state.target = target;
  state.repair = parseDiff(state.problem.repair_patch);
  state.trees = {};
  state.traceView = { selected: null, lanesByFile: false, pendingStep: null };
  startBrowser(target);
  startEvidence(target);
  document.title = `${target.instance_id} review`;

  const tabBar = (tabs, attribute) => tabs.map(([key, name]) => `
    <button type="button" role="tab" ${attribute}="${key}">${name}</button>`).join("");
  app.innerHTML = `
    <div class="review" style="--sidebar: ${sidebarWidth()}px">
      <aside class="rail" aria-label="Questions to rate">
        <div class="rail-head">
          <a href="#/">All problems</a>
          <h1>${STAGE_NAMES[target.stage]}</h1>
          <p class="instance">${esc(target.instance_id)}</p>
          <p>${esc(target.id)}</p>
        </div>
        <ol class="questions" id="questions"></ol>
        <div class="rail-foot" id="rail-foot"></div>
      </aside>
      <section class="main">
        <nav class="tabs" role="tablist" aria-label="What the judge rated">${tabBar(MIDDLE_TABS[target.stage], "data-tab")}</nav>
        <div class="panel" id="panel" role="tabpanel"></div>
      </section>
      <div class="divider" id="divider" role="separator" aria-orientation="vertical" aria-label="Resize the sidebar"></div>
      <aside class="sidebar">
        <nav class="tabs" role="tablist" aria-label="Reference">${tabBar(SIDE_TABS[target.stage], "data-side-tab")}</nav>
        <div class="panel" id="side-panel" role="tabpanel"></div>
      </aside>
    </div>`;

  app.querySelector(".main .tabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (button) showTab(button.dataset.tab);
  });
  app.querySelector(".sidebar .tabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-side-tab]");
    if (button) showSideTab(button.dataset.sideTab);
  });
  app.querySelector(".review").addEventListener("click", followLink);
  startDivider(app.querySelector(".review"));
  startRail(target);
  showTab(MIDDLE_TABS[target.stage][0][0]);
  showSideTab("problem");
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

export function showSideTab(tab) {
  state.sideTab = tab;
  for (const button of app.querySelectorAll("[data-side-tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.sideTab === tab));
    if (button.dataset.sideTab === "event") {
      const selected = state.traceView.selected;
      button.textContent = selected === null ? "Event" : `Event · step ${state.trace.data.events[selected].step}`;
    }
  }
  const panel = document.getElementById("side-panel");
  panel.scrollTop = 0;
  RENDER_SIDE[tab](panel);
}

// The sidebar's width follows the divider while it is dragged, and is remembered.
function sidebarWidth() {
  try {
    return Number(localStorage.getItem(SIDEBAR_WIDTH)) || 480;
  } catch {
    return 480;
  }
}

function startDivider(layout) {
  const divider = layout.querySelector("#divider");
  divider.addEventListener("pointerdown", (start) => {
    start.preventDefault();
    divider.setPointerCapture(start.pointerId);
    layout.classList.add("resizing");
    const move = (event) => {
      const width = Math.min(Math.max(layout.getBoundingClientRect().right - event.clientX, 300), 900);
      layout.style.setProperty("--sidebar", `${width}px`);
    };
    const stop = () => {
      divider.removeEventListener("pointermove", move);
      layout.classList.remove("resizing");
      try {
        localStorage.setItem(SIDEBAR_WIDTH, String(parseInt(layout.style.getPropertyValue("--sidebar"), 10)));
      } catch {
        // The width resets next time.
      }
    };
    divider.addEventListener("pointermove", move);
    divider.addEventListener("pointerup", stop, { once: true });
  });
}

// Links anywhere on the page open the file, test, evidence, or trace step they name.
function followLink(event) {
  const link = event.target.closest("[data-open-file], [data-open-test], [data-open-evidence], [data-open-step]");
  if (!link) return;
  const { openFile: path, openTest: testId, openEvidence: name, openStep: step, view, line } = link.dataset;
  if (step) openStep(Number(step));
  else if (testId) openTest(testId);
  else if (path) openFile(path, { view: view || "buggy", line: Number(line) || null });
  else openEvidence(name);
}

// The Test files tab: each generated file's versions, from the run's trace.
async function renderTestFiles(panel) {
  panel.innerHTML = `<p class="message">Loading the agent trace</p>`;
  try {
    const trace = await loadTrace();
    if (state.tab !== "tests") return;
    panel.innerHTML = `<div class="tests-tab trace-ui"></div>`;
    showTests(panel.firstElementChild, trace, { onSelectStep: openStep });
  } catch (error) {
    panel.innerHTML = `<p class="message error">${esc(error.message)}</p>`;
  }
}
