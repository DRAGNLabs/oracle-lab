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

// Markdown as HTML, with the review page's pinned marked.
export function markdown(text) {
  const normalized = (text || "").replaceAll("\r\n", "\n"); // dataset issues often use CRLF
  return window.marked ? marked.parse(normalized) : `<pre>${esc(normalized)}</pre>`;
}

// A unified diff with added and removed lines marked.
export function diffHtml(diff) {
  return diff
    .split("\n")
    .map((line) => {
      const kind = line.startsWith("diff --git") ? "file"
        : line.startsWith("+") && !line.startsWith("+++") ? "added"
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

// File categories as the page names them.
export const CATEGORY_LABELS = {
  source: "source",
  test: "existing test",
  generated_test: "generated test",
  docs: "docs",
  changelog: "changelog",
  config: "config",
  outside: "outside repo",
};

// What each color, tag, and fact on the page means. Tags show these on hover, and the
// Legend tab lists them all, so the wording lives only here.
export const DESCRIPTIONS = {
  action: {
    read: "Read a file, with a tool or a shell command such as cat, sed, or head.",
    search: "Searched files by text or name: grep, rg, find, ls, or a search tool.",
    edit_test: "Wrote or changed a test file: under the generated test directory, or a test file by path.",
    edit_source: "Wrote or changed a file that is not a test.",
    run_tests: "Ran pytest.",
    probe_code: "Ran Python that is not pytest: python -c, python - <<EOF, or a script.",
    run_other: "Ran any other shell command.",
    reasoning: "The model's reasoning between tool calls, raw or summarized depending on the harness.",
    message: "Text the model wrote to the user.",
    failed_call: "A tool call the harness aborted or rejected. Shell commands that exit nonzero keep their own type.",
  },
  overlap: {
    lines: "The event read, searched, or edited lines that the golden patch changes.",
    file: "The event touched a file the golden patch changes, but not the changed lines, or the lines are unknown.",
  },
  tag: {
    patched_name: "The event mentions a function or class that the golden patch changes.",
    git_history: "The command reads git history: git log, git show, or git blame.",
    exit: "The command's nonzero exit code. A chained command's exit code belongs to the whole chain.",
    test: "Touches an existing test file in the repository, not one the agent created.",
    docs: "Touches documentation: docs/, *.rst, *.md, or README.",
    changelog: "Touches a changelog: HISTORY, CHANGES, or CHANGELOG.",
    config: "Touches a configuration or build file.",
    outside: "Touches a path outside the repository.",
  },
  fact: {
    after_failures: "Before this edit, a pytest run reported failing tests in this file. The count is how many.",
    after_failed_run: "Before this edit, a pytest run reported failures, but none in this file.",
    adds_skip: "This edit added a skip or xfail mark.",
    failed_then_absent: "A pytest run reported this test as failing, and the submitted file no longer defines it.",
  },
  file: {
    matches_submission: "The last version the viewer rebuilt is identical to the submitted file.",
    differs_submission: "The last version the viewer rebuilt differs from the submitted file, so a change happened that the viewer could not follow.",
  },
  outcome: {
    fail_on_buggy_pass_on_golden: "Fails on the buggy code and passes on the fixed code: the test detects the bug.",
    pass_on_both: "Passes on both versions: the test does not distinguish them.",
    pass_on_buggy_fail_on_golden: "Passes on the buggy code and fails on the fixed code.",
    fail_on_both: "Fails on both versions.",
  },
};

// A tag with its description on hover.
export function tag(label, description, kind = "") {
  return `<span class="tag ${kind}" data-tip="${esc(description)}">${esc(label)}</span>`;
}

// The edit facts a test file version carries, as tags.
export function editFacts(version) {
  const facts = [];
  if (version.follows_failures.length) {
    facts.push(tag(`after failures in this file (${version.follows_failures.length})`, DESCRIPTIONS.fact.after_failures, "fact"));
  } else if (version.follows_failed_run) {
    facts.push(tag("after a run with failures", DESCRIPTIONS.fact.after_failed_run, "fact"));
  }
  if (version.skip_marks_added.length) facts.push(tag("adds skip/xfail", DESCRIPTIONS.fact.adds_skip, "fact"));
  return facts;
}

// A pytest summary's counts as words, such as "3 passed, 1 failed".
export function counts(summary) {
  return Object.entries(summary.counts).map(([word, count]) => `${count} ${word}`).join(", ") || "no tests ran";
}

// A test id without its file, such as `TestX::test_y`.
export function testName(id) {
  return id.split("::").slice(1).join("::") || id;
}

// A matrix cell, or a "buggy / golden" pair for incomplete outcomes, as a tag.
export function outcomeTag(cell) {
  return `<span class="outcome outcome-${esc(cell)}"${DESCRIPTIONS.outcome[cell] ? ` data-tip="${esc(DESCRIPTIONS.outcome[cell])}"` : ""}>${esc(cell.replaceAll("_", " "))}</span>`;
}
