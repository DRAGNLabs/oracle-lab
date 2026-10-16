// The review page. One rater rates one LLM output at a time against its evidence.
// Plain JavaScript, no build step. `hljs` and `marked` are loaded from vendor/.

const RATINGS = ["correct", "defensible", "incorrect"];
const RATING_NAMES = { correct: "Correct", defensible: "Defensible", incorrect: "Incorrect" };
const STAGE_NAMES = { judge: "Test judgment", classification: "Task classification" };
const TOUCH_NAMES = { repair: "repair", reference_tests: "reference tests", submission: "generated" };
const CELLS = {
  fail_on_buggy_pass_on_golden: "Fails on buggy, passes on golden",
  pass_on_both: "Passes on both",
  fail_on_both: "Fails on both",
  pass_on_buggy_fail_on_golden: "Passes on buggy, fails on golden",
};
const HELP_SEEN = "oracle-review:help-seen";
const HIGHLIGHT_LIMIT = 400_000; // Larger files are shown without syntax colors.

const app = document.getElementById("app");
const state = { listing: null, target: null };

// ---------- Startup and routing ----------

configureMarkdown();
document.getElementById("help-button").addEventListener("click", showHelp);
document.addEventListener("keydown", onKey);
window.addEventListener("hashchange", route);
route();

async function route() {
  const hash = location.hash.slice(1) || "/";
  try {
    if (!state.listing) {
      state.listing = await api("/api/targets");
      document.getElementById("rater").textContent = `Rating as ${state.listing.rater}`;
    }
    if (hash.startsWith("/review/")) {
      await openTarget(decodeURIComponent(hash.slice("/review/".length)));
    } else {
      showListing();
    }
  } catch (error) {
    app.innerHTML = `<p class="message error">${esc(error.message)}</p>`;
  }
}

async function api(path, params = {}, options = {}) {
  const query = new URLSearchParams(params).toString();
  const response = await fetch(query ? `${path}?${query}` : path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed: ${response.status}`);
  return body;
}

// ---------- Target list ----------

function showListing() {
  state.target = null;
  document.title = "Oracle Lab review";
  const { targets, skipped } = state.listing;
  const done = targets.filter((target) => target.reviewed).length;
  const groups = new Map();
  for (const target of targets) {
    if (!groups.has(target.instance_id)) groups.set(target.instance_id, []);
    groups.get(target.instance_id).push(target);
  }

  const sections = [...groups].map(([instance, items]) => `
    <section class="instance">
      <h2>${esc(instance)}</h2>
      <ul class="targets">${items.map(targetRow).join("")}</ul>
    </section>`).join("");
  const skippedSection = skipped.length ? `
    <section class="skipped">
      <h2>Can't be reviewed</h2>
      <ul class="targets">${skipped.map((item) => `
        <li><span class="path">${esc(item.id)}</span><span class="reason">${esc(item.reason)}</span></li>`).join("")}
      </ul>
    </section>` : "";
  const percent = targets.length ? (100 * done) / targets.length : 0;

  app.innerHTML = `
    <div class="listing">
      <h1>Targets to review</h1>
      <p class="progress-line">${done} of ${targets.length} reviewed</p>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${targets.length}"
        aria-valuenow="${done}"><span style="width: ${percent}%"></span></div>
      ${targets.length ? sections : `<p class="message">There is nothing to review in this directory.</p>`}
      ${skippedSection}
    </div>`;
  if (!localStorage.getItem(HELP_SEEN)) showHelp();
}

function targetRow(target) {
  return `
    <li><a class="target-row ${target.reviewed ? "done" : ""}" href="#/review/${encodeURIComponent(target.id)}">
      <span>${STAGE_NAMES[target.stage]}</span>
      <span class="path">${esc(target.id)}</span>
      <span class="status">${target.reviewed ? "Reviewed" : "Not reviewed"}</span>
    </a></li>`;
}

// ---------- One target ----------

async function openTarget(id) {
  app.innerHTML = `<p class="message">Loading ${esc(id)}</p>`;
  const target = await api("/api/target", { id });
  const draft = readDraft(target);
  state.target = target;
  state.ratings = draft || { ...(target.review || {}) };
  state.selected = Math.max(0, target.questions.findIndex((q) => !state.ratings[q.key]));
  state.tab = "overview";
  state.trees = {};
  state.files = { view: target.views[0], path: null, line: null, filter: "", touchedOnly: true, compare: false };
  state.log = { name: null, query: "", current: -1 };
  state.repair = parseDiff(target.repair_patch);
  document.title = `${target.instance_id} review`;

  const tabs = [["overview", "Overview"], ["files", "Files"]];
  tabs.push(["logs", target.stage === "judge" ? "Agent session" : "Task record"]);
  tabs.push(["rubric", "Stage rubric"]);

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
  const questions = document.getElementById("questions");
  questions.addEventListener("click", onQuestionClick);
  questions.addEventListener("click", onEvidenceLink);
  renderQuestions();
  renderFoot();
  showTab("overview");
}

function onQuestionClick(event) {
  const card = event.target.closest(".question");
  if (!card) return;
  const index = Number(card.dataset.index);
  const button = event.target.closest("[data-rating]");
  if (button) rate(index, button.dataset.rating, false);
  else select(index);
}

function renderQuestions() {
  const { target, ratings, selected } = state;
  document.getElementById("questions").innerHTML = target.questions.map((question, index) => {
    const rating = ratings[question.key];
    const answers = Array.isArray(question.answer) ? question.answer : [question.answer];
    return `
      <li class="question ${index === selected ? "selected" : ""} ${rating ? `rated-${rating}` : ""}"
        data-index="${index}">
        <h2>${esc(question.label)}</h2>
        <div class="answer">
          <span class="answer-intro">Judge answered</span>
          ${answers.map((answer) => `<span class="answer-value">${esc(answer)}</span>`).join("")}
        </div>
        <p class="justification">${linkify(question.justification)}</p>
        <div class="rate" role="radiogroup" aria-label="Your rating of this answer">
          ${RATINGS.map((key, n) => `
            <button type="button" role="radio" class="rate-${key}" data-rating="${key}"
              aria-checked="${rating === key}" data-tip="${esc(target.rating_definitions[key])}">
              <kbd>${n + 1}</kbd>${RATING_NAMES[key]}
            </button>`).join("")}
        </div>
      </li>`;
  }).join("");
}

function select(index) {
  const count = state.target.questions.length;
  state.selected = Math.min(Math.max(index, 0), count - 1);
  renderQuestions();
  document.querySelector(".question.selected")?.scrollIntoView({ block: "nearest" });
}

function rate(index, rating, advance) {
  const question = state.target.questions[index];
  state.ratings[question.key] = rating;
  writeDraft();
  // From the keyboard, move on to the next unrated question.
  let next = index;
  if (advance) {
    const unrated = state.target.questions.findIndex((q, i) => i > index && !state.ratings[q.key]);
    next = unrated === -1 ? index : unrated;
  }
  state.selected = next;
  renderQuestions();
  renderFoot();
  document.querySelector(".question.selected")?.scrollIntoView({ block: "nearest" });
}

function renderFoot(saved = false, error = "") {
  const { target, ratings } = state;
  const total = target.questions.length;
  const rated = target.questions.filter((q) => ratings[q.key]).length;
  const complete = rated === total;
  const changed = !sameRatings(ratings, target.review);

  let status = `${rated} of ${total} rated`;
  if (complete && changed) status = target.review ? "Changed, not saved yet" : "All rated, not saved yet";
  if (complete && !changed) status = "Saved";
  const next = !changed && target.review ? nextTarget() : null;
  const nextLink = next
    ? `<a class="next-link" href="#/review/${encodeURIComponent(next.id)}">Next target</a>`
    : !changed && target.review ? `<a class="next-link" href="#/">Every target is reviewed</a>` : "";

  document.getElementById("rail-foot").innerHTML = `
    <p class="save-status ${complete && !changed ? "saved" : ""}">${status}</p>
    <button type="button" class="primary" id="save" ${complete && changed ? "" : "disabled"}>
      ${target.review ? "Save changes" : "Save review"}
    </button>
    <p class="save-error" role="alert">${esc(error)}</p>
    ${nextLink}`;
  document.getElementById("save").addEventListener("click", saveReview);
  if (saved) document.querySelector(".next-link")?.focus();
}

async function saveReview() {
  const { target, ratings } = state;
  try {
    const result = await api("/api/review", { id: target.id }, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ratings }),
    });
    target.review = result.review;
    clearDraft();
    const entry = state.listing.targets.find((item) => item.id === target.id);
    if (entry) entry.reviewed = true;
    renderFoot(true);
  } catch (error) {
    renderFoot(false, error.message);
  }
}

function nextTarget() {
  const targets = state.listing.targets;
  const start = targets.findIndex((item) => item.id === state.target.id);
  for (let step = 1; step < targets.length; step++) {
    const item = targets[(start + step) % targets.length];
    if (!item.reviewed) return item;
  }
  return null;
}

function sameRatings(a, b) {
  const left = a || {};
  const right = b || {};
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  return [...keys].every((key) => left[key] === right[key]);
}

// Unsaved ratings live in this browser until saved.
function draftKey(target) {
  return `oracle-review:${state.listing.rater}:${target.id}`;
}
function readDraft(target) {
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey(target)) || "null");
    const keys = new Set(target.questions.map((q) => q.key));
    if (!draft) return null;
    return Object.fromEntries(Object.entries(draft).filter(([key, value]) => keys.has(key) && RATINGS.includes(value)));
  } catch {
    return null;
  }
}
function writeDraft() {
  try {
    if (sameRatings(state.ratings, state.target.review)) clearDraft();
    else localStorage.setItem(draftKey(state.target), JSON.stringify(state.ratings));
  } catch {
    // Without storage, ratings still work until the tab closes.
  }
}
function clearDraft() {
  try {
    localStorage.removeItem(draftKey(state.target));
  } catch {
    // Nothing was stored.
  }
}

// ---------- Tabs ----------

function showTab(tab) {
  state.tab = tab;
  for (const button of app.querySelectorAll("[data-tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  }
  const panel = document.getElementById("panel");
  panel.scrollTop = 0;
  if (tab === "overview") renderOverview(panel);
  else if (tab === "files") renderFiles(panel);
  else if (tab === "logs") renderLogs(panel);
  else renderRubric(panel);
}

function renderOverview(panel) {
  const target = state.target;
  const parts = [];
  if (target.stage === "judge") {
    const { submission, outcomes } = target;
    if (!submission.compliant || submission.forbidden_changes.length) {
      parts.push(`
        <div class="banner" role="note">
          The agent changed files outside the generated test directory, so this result is
          diagnostic only.
          <ul>${submission.forbidden_changes.map((path) => `<li>${esc(path)}</li>`).join("")}</ul>
        </div>`);
    }
    parts.push(`<section><h2>Issue</h2><article class="prose">${markdown(target.issue)}</article></section>`);
    parts.push(`<section><h2>Test outcomes</h2>${matrixTable(outcomes.matrix)}${outcomeTable(outcomes)}</section>`);
    parts.push(`
      <section><h2>Generated tests</h2>
        ${submission.files.length ? submission.files.map((path) => `
          <h3><button type="button" class="link-like" data-open-file="${esc(path)}">${esc(path)}</button></h3>
          <div class="generated" data-path="${esc(path)}"><p class="message">Loading</p></div>`).join("")
          : `<p>The agent captured no test files.</p>`}
      </section>`);
  } else {
    parts.push(`<section><h2>Issue</h2><article class="prose">${markdown(target.issue)}</article></section>`);
    for (const { title, text } of target.record_texts) {
      parts.push(`<section><h2>${esc(title)}</h2><article class="prose">${markdown(text)}</article></section>`);
    }
  }
  parts.push(`<section><h2>Repair</h2>${diffHtml(state.repair)}</section>`);
  parts.push(`
    <section><h2>Reference tests</h2>
      ${target.reference_test_ids.length ? `<h3>Reference test IDs</h3><ul>${target.reference_test_ids
        .map((id) => `<li><code>${esc(id)}</code></li>`).join("")}</ul>` : ""}
      ${diffHtml(parseDiff(target.reference_test_patch))}
    </section>`);

  panel.innerHTML = `<div class="overview">${parts.join("")}</div>`;
  panel.querySelector(".overview").addEventListener("click", onEvidenceLink);
  if (target.stage === "judge") loadGeneratedTests(panel);
}

async function loadGeneratedTests(panel) {
  for (const holder of panel.querySelectorAll(".generated")) {
    try {
      const file = await api("/api/file", { id: state.target.id, view: "buggy", path: holder.dataset.path });
      holder.innerHTML = fileBody(file, holder.dataset.path);
    } catch (error) {
      holder.innerHTML = `<p class="message error">${esc(error.message)}</p>`;
    }
  }
}

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
  const result = (value) => `<span class="${value === "pass" ? "pass" : value === "fail" ? "fail" : ""}">${esc(value)}</span>`;
  const failure = (name, value) => value ? `
    <details><summary>${name} failure</summary>
      <pre class="failure">${esc([value.exception_type, value.message].filter(Boolean).join(": ") || JSON.stringify(value, null, 2))}</pre>
    </details>` : "";
  return `
    <table class="outcomes">
      <thead><tr><th>Generated test</th><th>Buggy</th><th>Golden</th><th>Outcome</th></tr></thead>
      <tbody>${rows.map((row) => `
        <tr>
          <td class="test">
            <button type="button" class="link-like" data-open-test="${esc(row.test_id)}">${esc(row.test_id)}</button>
            ${failure("Buggy", row.buggy_failure)}${failure("Golden", row.golden_failure)}
          </td>
          <td>${result(row.buggy)}</td>
          <td>${result(row.golden)}</td>
          <td>${row.cell ? CELLS[row.cell] : "Incomplete"}</td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

function renderRubric(panel) {
  panel.innerHTML = `<div class="rubric"><article class="prose">${markdown(state.target.stage_rubric)}</article></div>`;
}

// ---------- Files ----------

function renderFiles(panel) {
  const { target, files } = state;
  panel.innerHTML = `
    <div class="files">
      <div class="files-side">
        <div class="files-controls">
          ${target.views.length > 1 ? `
            <div class="segmented" role="group" aria-label="Code version">
              ${target.views.map((view) => `
                <button type="button" data-view="${view}" aria-pressed="${files.view === view}">${view}</button>`).join("")}
            </div>` : ""}
          <input type="search" id="file-filter" placeholder="Filter files" value="${esc(files.filter)}" spellcheck="false">
          <label><input type="checkbox" id="touched-only" ${files.touchedOnly ? "checked" : ""}>
            ${target.stage === "judge"
              ? "Only files the repair, reference tests, or generated tests touch"
              : "Only files the repair or reference tests touch"}</label>
          <p class="hint"><kbd>Ctrl</kbd> <kbd>P</kbd> finds any file.</p>
        </div>
        <div class="tree" id="tree"><p class="note">Loading files</p></div>
      </div>
      <div class="file-view" id="file-view">
        <p class="message">Choose a file.</p>
      </div>
    </div>`;

  panel.querySelector(".files-controls").addEventListener("click", (event) => {
    const button = event.target.closest("[data-view]");
    if (!button) return;
    files.view = button.dataset.view;
    renderFiles(panel);
  });
  document.getElementById("file-filter").addEventListener("input", (event) => {
    files.filter = event.target.value;
    renderTree();
  });
  document.getElementById("touched-only").addEventListener("change", (event) => {
    files.touchedOnly = event.target.checked;
    renderTree();
  });
  const tree = document.getElementById("tree");
  tree.addEventListener("click", (event) => {
    const button = event.target.closest("[data-path]");
    if (button) openFile(button.dataset.path, { view: files.view });
  });
  // Folders of the full tree are filled in when first opened.
  tree.addEventListener("toggle", (event) => {
    const folder = event.target;
    if (folder.open && folder.dataset.dir !== undefined && !folder.dataset.filled) {
      folder.dataset.filled = "1";
      folder.querySelector(".children").innerHTML = folderHtml(findFolder(folder.dataset.dir), false);
    }
  }, true);
  panel.querySelector(".file-view").addEventListener("click", onEvidenceLink);
  panel.querySelector(".file-view").addEventListener("click", (event) => {
    if (event.target.closest("#compare")) {
      files.compare = !files.compare;
      showFile();
    }
  });

  loadTree(files.view).then(() => {
    renderTree();
    if (files.path) showFile();
  }, (error) => {
    tree.innerHTML = `<p class="note">${esc(error.message)}</p>`;
  });
}

async function loadTree(view) {
  if (!state.trees[view]) {
    const tree = document.getElementById("tree");
    if (tree) tree.innerHTML = `<p class="note">Fetching the repository. The first time can take a few seconds.</p>`;
    state.trees[view] = (await api("/api/tree", { id: state.target.id, view })).files;
  }
  return state.trees[view];
}

function renderTree() {
  const { files } = state;
  const all = state.trees[files.view] || [];
  const shown = files.touchedOnly ? all.filter((file) => file.touched.length) : all;
  const tree = document.getElementById("tree");
  const query = files.filter.trim().toLowerCase();

  if (query) {
    const matches = shown.filter((file) => file.path.toLowerCase().includes(query)).slice(0, 500);
    tree.innerHTML = matches.length
      ? `<div class="flat">${matches.map((file) => fileButton(file, file.path)).join("")}</div>`
      : `<p class="note">No file path contains "${esc(files.filter)}".</p>`;
    return;
  }
  if (!shown.length) {
    tree.innerHTML = `<p class="note">No files here. Clear the checkbox to see every file.</p>`;
    return;
  }
  state.folders = buildFolders(shown);
  // A short list opens fully; the whole repository opens one folder at a time.
  tree.innerHTML = folderHtml(state.folders, files.touchedOnly);
}

function buildFolders(files) {
  const root = { path: "", folders: new Map(), files: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.folders.has(part)) {
        node.folders.set(part, { path: node.path ? `${node.path}/${part}` : part, folders: new Map(), files: [] });
      }
      node = node.folders.get(part);
    }
    node.files.push(file);
  }
  return root;
}

function findFolder(path) {
  let node = state.folders;
  for (const part of path ? path.split("/") : []) node = node.folders.get(part);
  return node;
}

function folderHtml(node, eager) {
  const folders = [...node.folders.entries()].sort(([a], [b]) => a.localeCompare(b));
  const folderItems = folders.map(([name, child]) => `
    <details ${eager ? "open" : ""} data-dir="${esc(child.path)}" ${eager ? 'data-filled="1"' : ""}>
      <summary>${esc(name)}/</summary>
      <div class="children">${eager ? folderHtml(child, true) : ""}</div>
    </details>`).join("");
  const fileItems = node.files.map((file) => fileButton(file, file.path.split("/").pop())).join("");
  return folderItems + fileItems;
}

function fileButton(file, label) {
  const active = file.path === state.files.path ? "active" : "";
  return `<button type="button" class="file ${active}" data-path="${esc(file.path)}" title="${esc(file.path)}">
    <span>${esc(label)}</span>${file.touched.map(badge).join("")}</button>`;
}

function badge(touch) {
  return `<span class="badge ${touch}">${TOUCH_NAMES[touch]}</span>`;
}

async function openFile(path, { view, line } = {}) {
  const files = state.files;
  files.path = path;
  files.line = line || null;
  files.compare = false;
  if (view && state.target.views.includes(view)) files.view = view;
  if (state.tab !== "files") showTab("files");
  else {
    for (const button of document.querySelectorAll("#tree .file")) {
      button.classList.toggle("active", button.dataset.path === path);
    }
    showFile();
  }
}

async function showFile() {
  const { target, files } = state;
  const holder = document.getElementById("file-view");
  if (!holder || !files.path) return;
  const entry = (state.trees[files.view] || []).find((file) => file.path === files.path);
  const touched = entry ? entry.touched : [];
  const repaired = state.repair.some((diff) => diff.newPath === files.path || diff.oldPath === files.path);
  const compare = repaired && target.views.includes("golden");
  holder.innerHTML = `
    <div class="file-head">
      <h2>${esc(files.path)}</h2>
      <span class="view-name">${files.view}</span>
      ${touched.map(badge).join("")}
      ${compare ? `<button type="button" class="link-button" id="compare">
        ${files.compare ? "Show the whole file" : "Compare buggy and golden"}</button>` : ""}
    </div>
    <div id="file-body"><p class="message">Loading</p></div>`;
  const body = document.getElementById("file-body");

  if (files.compare) {
    const diff = state.repair.filter((d) => d.newPath === files.path || d.oldPath === files.path);
    body.innerHTML = splitDiffHtml(diff);
    return;
  }
  try {
    const file = await api("/api/file", { id: target.id, view: files.view, path: files.path });
    body.innerHTML = fileBody(file, files.path);
    if (files.line) {
      const row = body.querySelector(`[data-line="${files.line}"]`);
      row?.classList.add("target");
      row?.scrollIntoView({ block: "center" });
    }
  } catch (error) {
    // The file may not exist in this version, such as a file the repair creates.
    body.innerHTML = `<p class="message error">${esc(error.message)}</p>`;
  }
}

function fileBody(file, path) {
  if (file.binary) return `<p class="message">Binary file, ${formatSize(file.size)}. Not shown.</p>`;
  return codeHtml(file.text, path);
}

async function openTest(testId) {
  const path = testId.split("::")[0];
  const name = testId.split("::").pop().replace(/\[.*$/, "");
  let line = null;
  try {
    const file = await api("/api/file", { id: state.target.id, view: "buggy", path });
    const lines = (file.text || "").split("\n");
    const pattern = new RegExp(`^\\s*(async\\s+)?def\\s+${name.replace(/[^\w]/g, "")}\\b`);
    const index = lines.findIndex((text) => pattern.test(text));
    line = index === -1 ? null : index + 1;
  } catch {
    // Open the file anyway; showFile reports why it cannot be read.
  }
  openFile(path, { view: "buggy", line });
}

// ---------- Finder (Ctrl+P) ----------

const finder = document.getElementById("finder");
const finderInput = document.getElementById("finder-input");
const finderResults = document.getElementById("finder-results");
let finderMatches = [];
let finderIndex = 0;

async function openFinder() {
  if (!state.target) return;
  finderInput.value = "";
  finderResults.innerHTML = `<li class="empty">Loading files</li>`;
  finder.showModal();
  try {
    await loadTree(state.files.view);
    updateFinder();
  } catch (error) {
    finderResults.innerHTML = `<li class="empty">${esc(error.message)}</li>`;
  }
}

function updateFinder() {
  const query = finderInput.value.trim().toLowerCase();
  const files = state.trees[state.files.view] || [];
  finderMatches = files
    .map((file) => ({ file, score: query ? fuzzyScore(query, file.path) : file.touched.length ? 1 : 0 }))
    .filter((match) => match.score > 0 || (!query && match.score === 0))
    .sort((a, b) => b.score - a.score || a.file.path.length - b.file.path.length)
    .slice(0, 50);
  finderIndex = 0;
  renderFinder();
}

function renderFinder() {
  finderResults.innerHTML = finderMatches.length
    ? finderMatches.map((match, index) => `
        <li role="option" data-index="${index}" aria-selected="${index === finderIndex}">${esc(match.file.path)}</li>`).join("")
    : `<li class="empty">No file matches.</li>`;
  finderResults.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function chooseFinder(index) {
  const match = finderMatches[index];
  if (!match) return;
  finder.close();
  openFile(match.file.path, { view: state.files.view });
}

finderInput.addEventListener("input", updateFinder);
finderInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    finderIndex = Math.min(Math.max(finderIndex + step, 0), finderMatches.length - 1);
    renderFinder();
  } else if (event.key === "Enter") {
    event.preventDefault();
    chooseFinder(finderIndex);
  } else if (event.key === "Escape") {
    // A search box would spend Escape on clearing itself; close the finder instead.
    event.preventDefault();
    finder.close();
  }
});
finderResults.addEventListener("click", (event) => {
  const item = event.target.closest("[data-index]");
  if (item) chooseFinder(Number(item.dataset.index));
});

// Characters in order, scored higher when they are adjacent or start a path part.
function fuzzyScore(query, path) {
  const text = path.toLowerCase();
  let score = 0;
  let position = -1;
  for (const char of query) {
    const found = text.indexOf(char, position + 1);
    if (found === -1) return 0;
    score += found === position + 1 ? 3 : 1;
    if (found === 0 || "/_-.".includes(text[found - 1])) score += 2;
    position = found;
  }
  const base = text.slice(text.lastIndexOf("/") + 1);
  if (base.includes(query)) score += 10;
  return score;
}

// ---------- Agent session and other evidence ----------

function renderLogs(panel) {
  const names = state.target.evidence;
  const log = state.log;
  const first = ["evidence/agent-session.log", "instance.json"].find((name) => names.includes(name));
  if (!log.name) log.name = first || names[0];
  panel.innerHTML = `
    <div class="logs">
      <ul class="log-list">${names.map((name) => `
        <li><button type="button" data-log="${esc(name)}" aria-current="${name === log.name}">${esc(name)}</button></li>`).join("")}
      </ul>
      <div class="log-view">
        <div class="log-search">
          <input type="search" id="log-query" placeholder="Search this file" value="${esc(log.query)}" spellcheck="false">
          <span class="count" id="log-count"></span>
        </div>
        <pre class="log-text" id="log-text">Loading</pre>
      </div>
    </div>`;
  panel.querySelector(".log-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-log]");
    if (!button) return;
    log.name = button.dataset.log;
    log.current = -1;
    renderLogs(panel);
  });
  const input = document.getElementById("log-query");
  input.addEventListener("input", () => {
    log.query = input.value;
    log.current = -1;
    showLogText();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    stepLogMatch(event.shiftKey ? -1 : 1);
  });
  loadLog();
}

async function loadLog() {
  const log = state.log;
  try {
    const file = await api("/api/evidence", { id: state.target.id, name: log.name });
    let text = file.binary ? `Binary file, ${formatSize(file.size)}. Not shown.` : file.text;
    if (log.name.endsWith(".json")) {
      try {
        text = JSON.stringify(JSON.parse(text), null, 2);
      } catch {
        // Show it as saved.
      }
    }
    log.text = text;
    showLogText();
  } catch (error) {
    document.getElementById("log-text").textContent = error.message;
  }
}

function showLogText() {
  const { text, query } = state.log;
  const holder = document.getElementById("log-text");
  const count = document.getElementById("log-count");
  if (!holder || text === undefined) return;
  if (!query) {
    holder.textContent = text;
    count.textContent = "";
    return;
  }
  const lower = text.toLowerCase();
  const needle = query.toLowerCase();
  const pieces = [];
  let start = 0;
  let matches = 0;
  for (let found = lower.indexOf(needle); found !== -1 && matches < 5000; found = lower.indexOf(needle, found + needle.length)) {
    pieces.push(esc(text.slice(start, found)), `<mark>${esc(text.slice(found, found + needle.length))}</mark>`);
    start = found + needle.length;
    matches += 1;
  }
  pieces.push(esc(text.slice(start)));
  holder.innerHTML = pieces.join("");
  count.textContent = matches ? `${matches} found. Enter for next.` : "Not found";
  if (matches) stepLogMatch(1);
}

function stepLogMatch(step) {
  const marks = document.querySelectorAll("#log-text mark");
  if (!marks.length) return;
  const log = state.log;
  marks[log.current]?.classList.remove("current");
  log.current = (log.current + step + marks.length) % marks.length;
  marks[log.current].classList.add("current");
  marks[log.current].scrollIntoView({ block: "center" });
  document.getElementById("log-count").textContent = `${log.current + 1} of ${marks.length}`;
}

// ---------- Links from text to evidence ----------

// Paths a judge cited in its workspace open here: checkouts in Files, other evidence in its tab.
const WORKSPACE_PATH = /\/oracle-(?:judge|classification)\/(?:(buggy|golden|evidence|instance)\/)?([^\s`'"(),;<>]+)/g;

function linkify(text) {
  // Backtick spans become code; workspace paths in either become links.
  return String(text).split(/(`[^`]*`)/).map((part) => {
    const code = part.startsWith("`") && part.endsWith("`") && part.length > 1;
    const linked = linkPaths(code ? part.slice(1, -1) : part);
    return code ? `<code>${linked}</code>` : linked;
  }).join("");
}

function linkPaths(text) {
  let html = "";
  let start = 0;
  for (const match of text.matchAll(WORKSPACE_PATH)) {
    let [whole, area, rest] = match;
    const trailing = rest.match(/[.:]+$/)?.[0] || "";
    rest = rest.slice(0, rest.length - trailing.length);
    whole = whole.slice(0, whole.length - trailing.length);
    const lineMatch = rest.match(/^(.*?):(\d+)(?::\d+)?$/);
    const path = lineMatch ? lineMatch[1] : rest;
    const line = lineMatch ? lineMatch[2] : "";
    html += esc(text.slice(start, match.index));
    const attributes = area === "buggy" || area === "golden"
      ? `data-open-file="${esc(path)}" data-view="${area}" data-line="${line}"`
      : `data-open-log="${esc(area ? `${area}/${path}` : path)}"`;
    html += `<button type="button" class="link-like" ${attributes}>${esc(whole)}</button>`;
    start = match.index + whole.length;
  }
  return html + esc(text.slice(start));
}

function onEvidenceLink(event) {
  const link = event.target.closest("[data-open-file], [data-open-log], [data-open-test]");
  if (!link) return;
  event.stopPropagation();
  if (link.dataset.openTest) {
    openTest(link.dataset.openTest);
  } else if (link.dataset.openFile) {
    const line = Number(link.dataset.line) || null;
    openFile(link.dataset.openFile, { view: link.dataset.view || "buggy", line });
  } else if (state.target.evidence.includes(link.dataset.openLog)) {
    state.log.name = link.dataset.openLog;
    state.log.current = -1;
    showTab("logs");
  }
}

// ---------- Keyboard ----------

function onKey(event) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p" && state.target) {
    event.preventDefault();
    openFinder();
    return;
  }
  if (document.querySelector("dialog[open]")) return;
  const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
  if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === "?") {
    showHelp();
    return;
  }
  if (!state.target) return;
  const index = ["1", "2", "3"].indexOf(event.key);
  if (index !== -1) {
    event.preventDefault();
    rate(state.selected, RATINGS[index], true);
  } else if (event.key === "j") {
    select(state.selected + 1);
  } else if (event.key === "k") {
    select(state.selected - 1);
  }
}

function showHelp() {
  const definitions = state.target?.rating_definitions || state.listing?.rating_definitions;
  const list = document.getElementById("help-ratings");
  list.innerHTML = definitions
    ? RATINGS.map((key) => `<dt class="${key}">${RATING_NAMES[key]}</dt><dd>${esc(definitions[key])}</dd>`).join("")
    : "";
  const dialog = document.getElementById("help");
  if (!dialog.open) dialog.showModal();
  try {
    localStorage.setItem(HELP_SEEN, "1");
  } catch {
    // The dialog shows again next time.
  }
}

// ---------- Code, diffs, and Markdown ----------

const LANGUAGES = {
  py: "python", pyi: "python", md: "markdown", rst: "plaintext", txt: "plaintext",
  json: "json", yml: "yaml", yaml: "yaml", toml: "ini", cfg: "ini", ini: "ini",
  sh: "bash", js: "javascript", ts: "typescript", c: "c", h: "c", cpp: "cpp",
  html: "xml", xml: "xml", css: "css", diff: "diff", patch: "diff",
};

function languageOf(path) {
  const extension = path.split(".").pop().toLowerCase();
  const language = LANGUAGES[extension];
  return language && hljs.getLanguage(language) ? language : null;
}

function highlightLines(text, path) {
  const language = languageOf(path);
  if (!language || text.length > HIGHLIGHT_LIMIT) return text.split("\n").map(esc);
  return splitHighlighted(hljs.highlight(text, { language, ignoreIllegals: true }).value);
}

// highlight.js returns one HTML string; cut it into lines, closing and reopening spans.
function splitHighlighted(html) {
  const lines = [];
  const open = [];
  let current = "";
  for (const [, start, end, newline, text] of html.matchAll(/(<span[^>]*>)|(<\/span>)|(\n)|([^<\n]+)/g)) {
    if (start) {
      open.push(start);
      current += start;
    } else if (end) {
      open.pop();
      current += end;
    } else if (newline) {
      lines.push(current + "</span>".repeat(open.length));
      current = open.join("");
    } else {
      current += text;
    }
  }
  lines.push(current);
  return lines;
}

function codeHtml(text, path) {
  const lines = highlightLines(text.endsWith("\n") ? text.slice(0, -1) : text, path);
  return `<div class="code">${lines.map((line, index) => `
    <div class="row" data-line="${index + 1}"><span class="num">${index + 1}</span><span class="text">${line || " "}</span></div>`).join("")}
  </div>`;
}

// A unified diff, split into files and hunks with line numbers on both sides.
function parseDiff(patch) {
  const files = [];
  let file = null;
  let hunk = null;
  let oldLine = 0;
  let newLine = 0;
  for (const line of (patch || "").split("\n")) {
    if (line.startsWith("diff --git ")) {
      file = { oldPath: null, newPath: null, hunks: [] };
      files.push(file);
      hunk = null;
    } else if (!file) {
      continue;
    } else if (!hunk && line.startsWith("--- ")) {
      file.oldPath = diffPath(line.slice(4), "a/");
    } else if (!hunk && line.startsWith("+++ ")) {
      file.newPath = diffPath(line.slice(4), "b/");
    } else if (line.startsWith("@@")) {
      const match = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      oldLine = Number(match?.[1] || 0);
      newLine = Number(match?.[2] || 0);
      hunk = { header: line, lines: [] };
      file.hunks.push(hunk);
    } else if (hunk) {
      const sign = line[0];
      if (sign === "+") hunk.lines.push({ type: "add", text: line.slice(1), newNo: newLine++ });
      else if (sign === "-") hunk.lines.push({ type: "del", text: line.slice(1), oldNo: oldLine++ });
      else if (sign === " ") hunk.lines.push({ type: "same", text: line.slice(1), oldNo: oldLine++, newNo: newLine++ });
    }
  }
  return files;
}

function diffPath(text, prefix) {
  const path = text.split("\t")[0];
  if (path === "/dev/null") return null;
  return path.startsWith(prefix) ? path.slice(prefix.length) : path;
}

function diffHtml(files) {
  if (!files.length) return `<p>No changes.</p>`;
  return files.map((file) => {
    const path = file.newPath || file.oldPath;
    const language = languageOf(path);
    const highlight = (text) => language ? hljs.highlight(text, { language, ignoreIllegals: true }).value : esc(text);
    const name = file.oldPath && file.newPath && file.oldPath !== file.newPath
      ? `${file.oldPath} renamed to ${file.newPath}`
      : !file.oldPath ? `${path} (new file)` : !file.newPath ? `${path} (deleted)` : path;
    const rows = file.hunks.map((hunk) => `
      <div class="row hunk"><span class="num"></span><span class="num"></span><span class="sign"></span><span class="text">${esc(hunk.header)}</span></div>
      ${hunk.lines.map((line) => `
        <div class="row ${line.type}">
          <span class="num">${line.oldNo ?? ""}</span><span class="num">${line.newNo ?? ""}</span>
          <span class="sign">${line.type === "add" ? "+" : line.type === "del" ? "-" : ""}</span>
          <span class="text">${highlight(line.text) || " "}</span>
        </div>`).join("")}`).join("");
    const opener = file.newPath && state.target.views.includes("golden")
      ? `<button type="button" class="link-like" data-open-file="${esc(file.newPath)}" data-view="golden">${esc(name)}</button>`
      : file.oldPath ? `<button type="button" class="link-like" data-open-file="${esc(file.oldPath)}" data-view="buggy">${esc(name)}</button>`
        : esc(name);
    return `<div class="diff-file"><p class="diff-name">${opener}</p><div class="code diff">${rows}</div></div>`;
  }).join("");
}

// The repair's hunks for one file, buggy on the left and golden on the right.
function splitDiffHtml(files) {
  if (!files.length) return `<p class="message">The repair does not change this file.</p>`;
  const left = [];
  const right = [];
  for (const file of files) {
    const language = languageOf(file.newPath || file.oldPath);
    const highlight = (text) => language ? hljs.highlight(text, { language, ignoreIllegals: true }).value : esc(text);
    const row = (type, number, text) => text === null
      ? `<div class="row empty"><span class="num"></span><span class="text"> </span></div>`
      : `<div class="row ${type}"><span class="num">${number}</span><span class="text">${highlight(text) || " "}</span></div>`;
    for (const hunk of file.hunks) {
      const header = `<div class="row hunk"><span class="num"></span><span class="text">${esc(hunk.header)}</span></div>`;
      left.push(header);
      right.push(header);
      let removed = [];
      let added = [];
      const flush = () => {
        for (let i = 0; i < Math.max(removed.length, added.length); i++) {
          const del = removed[i];
          const add = added[i];
          left.push(del ? row("del", del.oldNo, del.text) : row("", "", null));
          right.push(add ? row("add", add.newNo, add.text) : row("", "", null));
        }
        removed = [];
        added = [];
      };
      for (const line of hunk.lines) {
        if (line.type === "del") removed.push(line);
        else if (line.type === "add") added.push(line);
        else {
          flush();
          left.push(row("", line.oldNo, line.text));
          right.push(row("", line.newNo, line.text));
        }
      }
      flush();
    }
  }
  return `
    <div class="split-heads"><span>Buggy</span><span>Golden</span></div>
    <div class="code split"><div class="side">${left.join("")}</div><div class="side">${right.join("")}</div></div>`;
}

function configureMarkdown() {
  // Issue text comes from outside the project, so raw HTML is shown as text and
  // only web links are kept.
  marked.use({
    gfm: true,
    renderer: {
      html({ text }) {
        return esc(text);
      },
      link({ href, title, tokens }) {
        const text = this.parser.parseInline(tokens);
        if (!/^https?:\/\//i.test(href)) return text;
        const titleAttribute = title ? ` title="${esc(title)}"` : "";
        return `<a href="${esc(href)}"${titleAttribute} target="_blank" rel="noopener noreferrer">${text}</a>`;
      },
      image({ href, text }) {
        return esc(text || href);
      },
      code({ text, lang }) {
        const language = lang && hljs.getLanguage(lang) ? lang : null;
        const body = language ? hljs.highlight(text, { language, ignoreIllegals: true }).value : esc(text);
        return `<pre><code class="hljs">${body}</code></pre>`;
      },
    },
  });
}

function markdown(text) {
  return marked.parse(text || "");
}

// ---------- Small helpers ----------

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
