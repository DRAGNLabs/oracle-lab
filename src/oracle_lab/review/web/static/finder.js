// The file finder: Ctrl+P, type part of a path, Enter to open it.

import { loadTree, openFile } from "./files.js";
import { esc } from "./render.js";
import { state } from "./state.js";

const finder = document.getElementById("finder");
const input = document.getElementById("finder-input");
const results = document.getElementById("finder-results");
let matches = [];
let chosen = 0;

input.addEventListener("input", update);
input.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    chosen = Math.min(Math.max(chosen + (event.key === "ArrowDown" ? 1 : -1), 0), matches.length - 1);
    render();
  } else if (event.key === "Enter") {
    event.preventDefault();
    choose(chosen);
  } else if (event.key === "Escape") {
    // A search box would spend Escape on clearing itself; close the finder instead.
    event.preventDefault();
    finder.close();
  }
});
results.addEventListener("click", (event) => {
  const item = event.target.closest("[data-index]");
  if (item) choose(Number(item.dataset.index));
});

export async function openFinder() {
  input.value = "";
  results.innerHTML = `<li class="empty">Loading files</li>`;
  finder.showModal();
  try {
    await loadTree(state.browser.view);
    update();
  } catch (error) {
    results.innerHTML = `<li class="empty">${esc(error.message)}</li>`;
  }
}

// With no query, files something touched come first.
function update() {
  const query = input.value.trim().toLowerCase();
  matches = (state.trees[state.browser.view] || [])
    .map((file) => ({ file, score: query ? fuzzyScore(query, file.path) : file.touched.length }))
    .filter((match) => !query || match.score > 0)
    .sort((a, b) => b.score - a.score || a.file.path.length - b.file.path.length)
    .slice(0, 50);
  chosen = 0;
  render();
}

function render() {
  results.innerHTML = matches.length
    ? matches.map((match, index) => `
        <li role="option" data-index="${index}" aria-selected="${index === chosen}">${esc(match.file.path)}</li>`).join("")
    : `<li class="empty">No file matches.</li>`;
  results.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function choose(index) {
  const match = matches[index];
  if (!match) return;
  finder.close();
  openFile(match.file.path, { view: state.browser.view });
}

// The query's characters in order, scored higher when they are adjacent, start a
// path part, or fall in the file name.
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
  if (text.slice(text.lastIndexOf("/") + 1).includes(query)) score += 10;
  return score;
}
