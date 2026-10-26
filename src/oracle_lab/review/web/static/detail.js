// The selected event in full: what the agent issued, what it saw, and what it touched.

import { DESCRIPTIONS, labels, counts } from "./labels.js";
import { code, esc } from "./render.js";

const OUTPUT_LINES = 400; // longer output is collapsed behind a toggle

// The caller shows the prompt to select an event when none is selected.
export function showDetail(container, trace, selected) {
  const event = trace.events[selected];
  const siblings = trace.events.filter((other) => other.step === event.step).length;
  const meta = [
    `Step ${event.step}${siblings > 1 ? ` (part ${event.part + 1} of ${siblings}; output shared)` : ""}`,
    `trace.jsonl line ${event.trace_line}`,
    event.timestamp ? new Date(event.timestamp).toLocaleTimeString() : "",
    event.exit_code !== null ? `exit ${event.exit_code}` : "",
  ].filter(Boolean).join(" · ");
  container.innerHTML = `
    <h3 data-tip="${esc(DESCRIPTIONS.action[event.action])}"><span class="dot" style="--dot: var(--a-${event.action})"></span>${labels().actions[event.action]}</h3>
    <p class="muted small">${esc(meta)}</p>
    <p>${esc(event.description)}</p>
    ${touches(event)}
    ${facts(event)}
    ${section("Text", event.text, event.action === "probe_code" ? "python" : null, event.action === "reasoning" || event.action === "message")}
    ${section("Command", event.command, "bash")}
    ${section("Output", event.output, null)}`;
  for (const button of container.querySelectorAll("[data-expand]")) {
    button.addEventListener("click", () => {
      button.previousElementSibling.classList.remove("clipped");
      button.remove();
    });
  }
  for (const block of container.querySelectorAll("pre code[data-language]")) {
    block.innerHTML = code(block.textContent, block.dataset.language);
  }
}

function touches(event) {
  if (!event.touches.length) return "";
  const rows = event.touches
    .map((touch) => {
      const lines = touch.lines ? `L${touch.lines[0]}–${touch.lines[1] >= 1e9 ? "end" : touch.lines[1]}` : "whole / unknown";
      return `<tr class="overlap-${touch.overlap}"><td><code>${esc(touch.path)}</code></td><td>${esc(labels().categories[touch.category] || touch.category)}</td><td>${lines}</td><td${touch.overlap !== "none" ? ` data-tip="${esc(DESCRIPTIONS.overlap[touch.overlap])}"` : ""}>${touch.overlap === "lines" ? "patched lines" : touch.overlap === "file" ? "patched file" : ""}</td></tr>`;
    })
    .join("");
  return `<table class="touches"><tr><th>File</th><th>Category</th><th>Lines</th><th>Patch</th></tr>${rows}</table>`;
}

function facts(event) {
  const items = [];
  if (event.patched_names.length) items.push(`Mentions functions or classes the golden patch changes: ${event.patched_names.map((name) => `<code>${esc(name)}</code>`).join(" ")}`);
  if (event.git_history) items.push("Reads git history");
  if (event.pytest) {
    items.push(`Pytest: ${esc(counts(event.pytest))}`);
    for (const id of event.pytest.failed) items.push(`FAILED <code>${esc(id)}</code>`);
  }
  return items.length ? `<ul class="facts">${items.map((item) => `<li>${item}</li>`).join("")}</ul>` : "";
}

function section(title, text, language, prose = false) {
  if (!text) return "";
  const lines = text.split("\n").length;
  const clipped = lines > OUTPUT_LINES;
  const body = prose
    ? `<div class="prose">${esc(text)}</div>`
    : `<pre class="${clipped ? "clipped" : ""}"><code${language ? ` data-language="${language}"` : ""}>${esc(text)}</code></pre>${clipped ? `<button type="button" data-expand>Show all ${lines} lines</button>` : ""}`;
  return `<h4>${title}</h4>${body}`;
}
