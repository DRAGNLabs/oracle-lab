// The evidence tab: the files the judge read besides the repository, with search.
// For a judgment that is chiefly the agent session; for a classification, the task record.

import { esc, formatSize } from "./render.js";
import { showTab } from "./review.js";
import { api, state } from "./state.js";

const OPEN_FIRST = ["evidence/agent-session.log", "instance.json"];
const MATCH_LIMIT = 5000;

export function startEvidence(target) {
  const first = OPEN_FIRST.find((name) => target.evidence.includes(name)) || target.evidence[0];
  state.evidence = { name: first, text: undefined, query: "", current: -1 };
}

// Show one evidence file, switching to the evidence tab.
export function openEvidence(name) {
  if (!state.target.evidence.includes(name)) return;
  Object.assign(state.evidence, { name, text: undefined, current: -1 });
  showTab("evidence");
}

export function renderEvidence(panel) {
  const evidence = state.evidence;
  panel.innerHTML = `
    <div class="evidence">
      <ul class="evidence-list">${state.target.evidence.map((name) => `
        <li><button type="button" data-name="${esc(name)}" aria-current="${name === evidence.name}">${esc(name)}</button></li>`).join("")}
      </ul>
      <div class="evidence-view">
        <div class="evidence-search">
          <input type="search" id="evidence-query" placeholder="Search this file" value="${esc(evidence.query)}" spellcheck="false">
          <span class="count" id="evidence-count"></span>
        </div>
        <pre class="evidence-text" id="evidence-text">Loading</pre>
      </div>
    </div>`;
  panel.querySelector(".evidence-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-name]");
    if (button) openEvidence(button.dataset.name);
  });
  const input = document.getElementById("evidence-query");
  input.addEventListener("input", () => {
    evidence.query = input.value;
    evidence.current = -1;
    showText();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    stepMatch(event.shiftKey ? -1 : 1);
  });
  loadText();
}

async function loadText() {
  const evidence = state.evidence;
  try {
    const file = await api("/api/evidence", { id: state.target.id, name: evidence.name });
    evidence.text = file.binary ? `Binary file, ${formatSize(file.size)}. Not shown.` : prettyJson(evidence.name, file.text);
    showText();
  } catch (error) {
    document.getElementById("evidence-text").textContent = error.message;
  }
}

function prettyJson(name, text) {
  if (!name.endsWith(".json")) return text;
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text; // Show it as saved.
  }
}

// The file's text, with every match of the search marked.
function showText() {
  const { text, query } = state.evidence;
  const holder = document.getElementById("evidence-text");
  const count = document.getElementById("evidence-count");
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
  for (let found = lower.indexOf(needle); found !== -1 && matches < MATCH_LIMIT; found = lower.indexOf(needle, start)) {
    pieces.push(esc(text.slice(start, found)), `<mark>${esc(text.slice(found, found + needle.length))}</mark>`);
    start = found + needle.length;
    matches += 1;
  }
  pieces.push(esc(text.slice(start)));
  holder.innerHTML = pieces.join("");
  count.textContent = matches ? `${matches} found. Enter for next.` : "Not found";
  if (matches) stepMatch(1);
}

function stepMatch(step) {
  const marks = document.querySelectorAll("#evidence-text mark");
  if (!marks.length) return;
  const evidence = state.evidence;
  marks[evidence.current]?.classList.remove("current");
  evidence.current = (evidence.current + step + marks.length) % marks.length;
  marks[evidence.current].classList.add("current");
  marks[evidence.current].scrollIntoView({ block: "center" });
  document.getElementById("evidence-count").textContent = `${evidence.current + 1} of ${marks.length}`;
}
