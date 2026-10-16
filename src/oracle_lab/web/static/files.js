// The Files tab: a tree of the repository version the judge read, and one file.

import { esc, fileHtml, splitDiffHtml } from "./render.js";
import { showTab } from "./review.js";
import { api, state } from "./state.js";

const TOUCH_NAMES = { repair: "repair", reference_tests: "reference tests", submission: "generated" };

export function startBrowser(target) {
  state.browser = { view: target.views[0], path: null, line: null, filter: "", touchedOnly: true, compare: false };
}

export function renderFiles(panel) {
  const { target, browser } = state;
  panel.innerHTML = `
    <div class="files">
      <div class="files-side">
        <div class="files-controls">
          ${target.views.length > 1 ? `
            <div class="segmented" role="group" aria-label="Code version">
              ${target.views.map((view) => `
                <button type="button" data-view="${view}" aria-pressed="${browser.view === view}">${view}</button>`).join("")}
            </div>` : ""}
          <input type="search" id="file-filter" placeholder="Filter files" value="${esc(browser.filter)}" spellcheck="false">
          <label><input type="checkbox" id="touched-only" ${browser.touchedOnly ? "checked" : ""}>
            Only files the ${target.stage === "judge" ? "repair, reference tests, or generated tests" : "repair or reference tests"} touch</label>
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
    browser.view = button.dataset.view;
    renderFiles(panel);
  });
  document.getElementById("file-filter").addEventListener("input", (event) => {
    browser.filter = event.target.value;
    renderTree();
  });
  document.getElementById("touched-only").addEventListener("change", (event) => {
    browser.touchedOnly = event.target.checked;
    renderTree();
  });
  const tree = document.getElementById("tree");
  tree.addEventListener("click", (event) => {
    const button = event.target.closest("[data-path]");
    if (button) openFile(button.dataset.path, { view: browser.view });
  });
  // Folders of the full tree are filled in when first opened.
  tree.addEventListener("toggle", (event) => {
    const folder = event.target;
    if (folder.open && folder.dataset.dir !== undefined && !folder.dataset.filled) {
      folder.dataset.filled = "1";
      folder.querySelector(".children").innerHTML = folderHtml(findFolder(folder.dataset.dir), false);
    }
  }, true);
  document.getElementById("file-view").addEventListener("click", (event) => {
    if (!event.target.closest("#compare")) return;
    browser.compare = !browser.compare;
    showFile();
  });

  loadTree(browser.view).then(() => {
    renderTree();
    if (browser.path) showFile();
  }, (error) => {
    tree.innerHTML = `<p class="note">${esc(error.message)}</p>`;
  });
}

// One view's file list, fetched once. The first fetch of a repository can be slow.
export async function loadTree(view) {
  if (!state.trees[view]) {
    const tree = document.getElementById("tree");
    if (tree) tree.innerHTML = `<p class="note">Fetching the repository. The first time can take a few seconds.</p>`;
    state.trees[view] = (await api("/api/tree", { id: state.target.id, view })).files;
  }
  return state.trees[view];
}

// Show a file in the Files tab, switching to it if needed.
export function openFile(path, { view, line } = {}) {
  const browser = state.browser;
  browser.path = path;
  browser.line = line || null;
  browser.compare = false;
  if (view && state.target.views.includes(view)) browser.view = view;
  if (state.tab !== "files") {
    showTab("files");
    return;
  }
  for (const button of document.querySelectorAll("#tree .file")) {
    button.classList.toggle("active", button.dataset.path === path);
  }
  showFile();
}

// Open a generated test at its definition, read from the buggy view.
export async function openTest(testId) {
  const path = testId.split("::")[0];
  const name = testId.split("::").pop().replace(/\[.*$/, "").replace(/[^\w]/g, "");
  let line = null;
  try {
    const file = await api("/api/file", { id: state.target.id, view: "buggy", path });
    const definition = new RegExp(`^\\s*(async\\s+)?def\\s+${name}\\b`);
    const index = (file.text || "").split("\n").findIndex((text) => definition.test(text));
    line = index === -1 ? null : index + 1;
  } catch {
    // Open the file anyway; the file view reports why it cannot be read.
  }
  openFile(path, { view: "buggy", line });
}

function renderTree() {
  const browser = state.browser;
  const all = state.trees[browser.view] || [];
  const shown = browser.touchedOnly ? all.filter((file) => file.touched.length) : all;
  const tree = document.getElementById("tree");
  const query = browser.filter.trim().toLowerCase();

  if (query) {
    const matches = shown.filter((file) => file.path.toLowerCase().includes(query)).slice(0, 500);
    tree.innerHTML = matches.length
      ? `<div class="flat">${matches.map((file) => fileButton(file, file.path)).join("")}</div>`
      : `<p class="note">No file path contains "${esc(browser.filter)}".</p>`;
    return;
  }
  if (!shown.length) {
    tree.innerHTML = `<p class="note">No files here. Clear the checkbox to see every file.</p>`;
    return;
  }
  browser.folders = buildFolders(shown);
  // A short list opens fully; the whole repository opens one folder at a time.
  tree.innerHTML = folderHtml(browser.folders, browser.touchedOnly);
}

function buildFolders(files) {
  const root = { path: "", folders: new Map(), files: [] };
  for (const file of files) {
    let node = root;
    for (const part of file.path.split("/").slice(0, -1)) {
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
  let node = state.browser.folders;
  for (const part of path ? path.split("/") : []) node = node.folders.get(part);
  return node;
}

function folderHtml(node, eager) {
  const folders = [...node.folders.entries()].sort(([a], [b]) => a.localeCompare(b));
  const folderItems = folders.map(([name, child]) => `
    <details ${eager ? 'open data-filled="1"' : ""} data-dir="${esc(child.path)}">
      <summary>${esc(name)}/</summary>
      <div class="children">${eager ? folderHtml(child, true) : ""}</div>
    </details>`).join("");
  const fileItems = node.files.map((file) => fileButton(file, file.path.split("/").pop())).join("");
  return folderItems + fileItems;
}

function fileButton(file, label) {
  const active = file.path === state.browser.path ? "active" : "";
  return `<button type="button" class="file ${active}" data-path="${esc(file.path)}" title="${esc(file.path)}">
    <span>${esc(label)}</span>${file.touched.map(badge).join("")}</button>`;
}

function badge(touch) {
  return `<span class="badge ${touch}">${TOUCH_NAMES[touch]}</span>`;
}

async function showFile() {
  const { target, browser } = state;
  const holder = document.getElementById("file-view");
  if (!holder || !browser.path) return;
  const entry = (state.trees[browser.view] || []).find((file) => file.path === browser.path);
  const repairDiff = state.repair.filter((d) => d.newPath === browser.path || d.oldPath === browser.path);
  const canCompare = repairDiff.length > 0 && target.views.includes("golden");
  holder.innerHTML = `
    <div class="file-head">
      <h2>${esc(browser.path)}</h2>
      <span class="view-name">${browser.view}</span>
      ${(entry ? entry.touched : []).map(badge).join("")}
      ${canCompare ? `<button type="button" class="link-button" id="compare">
        ${browser.compare ? "Show the whole file" : "Compare buggy and golden"}</button>` : ""}
    </div>
    <div id="file-body"><p class="message">Loading</p></div>`;
  const body = document.getElementById("file-body");

  if (browser.compare) {
    body.innerHTML = splitDiffHtml(repairDiff);
    return;
  }
  try {
    body.innerHTML = fileHtml(await api("/api/file", { id: target.id, view: browser.view, path: browser.path }), browser.path);
    const row = browser.line && body.querySelector(`[data-line="${browser.line}"]`);
    row?.classList.add("target");
    row?.scrollIntoView({ block: "center" });
  } catch (error) {
    // The file may not exist in this version, such as a file the repair creates.
    body.innerHTML = `<p class="message error">${esc(error.message)}</p>`;
  }
}
