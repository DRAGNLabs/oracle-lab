// Small helpers for building HTML strings safely.

export function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// Highlighted code, or escaped text when highlight.js does not know the language.
export function code(text, language) {
  if (window.hljs && language && hljs.getLanguage(language)) {
    return hljs.highlight(text, { language, ignoreIllegals: true }).value;
  }
  return esc(text);
}

// A unified diff with added and removed lines marked.
export function diffHtml(diff) {
  return diff
    .split("\n")
    .map((line) => {
      const kind = line.startsWith("+") && !line.startsWith("+++") ? "added"
        : line.startsWith("-") && !line.startsWith("---") ? "removed"
        : line.startsWith("@@") ? "hunk" : "";
      return `<span class="diff-line ${kind}">${esc(line) || " "}</span>`;
    })
    .join("");
}

export const ACTION_LABELS = {
  read: "Read",
  search: "Search",
  edit_test: "Edit test",
  edit_source: "Edit source",
  run_tests: "Run tests",
  probe_code: "Probe code",
  run_other: "Run other",
  reasoning: "Reasoning",
  message: "Message",
  failed_call: "Failed call",
};

// The edit facts a test file version carries, as short neutral labels.
export function editFacts(version) {
  const facts = [];
  if (version.follows_failures.length) {
    facts.push(`after failures in this file (${version.follows_failures.length})`);
  } else if (version.follows_failed_run) {
    facts.push("after a run with failures");
  }
  if (version.skip_marks_added.length) facts.push("adds skip/xfail");
  return facts;
}
