// HTML shared by every view: escaping, Markdown, highlighted code, diffs, and links
// to evidence. `hljs` and `marked` are the vendored libraries loaded before the page.

export const STAGE_NAMES = { judge: "Test judgment", classification: "Task classification" };

const HIGHLIGHT_LIMIT = 400_000; // Larger files are shown without syntax colors.
const LANGUAGES = {
  py: "python", pyi: "python", md: "markdown", rst: "plaintext", txt: "plaintext",
  json: "json", yml: "yaml", yaml: "yaml", toml: "ini", cfg: "ini", ini: "ini",
  sh: "bash", js: "javascript", ts: "typescript", c: "c", h: "c", cpp: "cpp",
  html: "xml", xml: "xml", css: "css", diff: "diff", patch: "diff",
};
// Paths a judge cited in its workspace: checkouts open in Files, other files in evidence.
const WORKSPACE_PATH = /\/oracle-(?:judge|classification)\/(?:(buggy|golden|evidence|instance)\/)?([^\s`'"(),;<>]+)/g;

export function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// ---------- Markdown ----------

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
      return `<pre><code class="hljs">${highlight(text, language)}</code></pre>`;
    },
  },
});

export function markdown(text) {
  return marked.parse(text || "");
}

// ---------- Code ----------

function languageOf(path) {
  const language = LANGUAGES[path.split(".").pop().toLowerCase()];
  return language && hljs.getLanguage(language) ? language : null;
}

function highlight(text, language) {
  return language ? hljs.highlight(text, { language, ignoreIllegals: true }).value : esc(text);
}

// A file with line numbers, or a note that it is binary.
export function fileHtml(file, path) {
  if (file.binary) return `<p class="message">Binary file, ${formatSize(file.size)}. Not shown.</p>`;
  const text = file.text.endsWith("\n") ? file.text.slice(0, -1) : file.text;
  const language = text.length > HIGHLIGHT_LIMIT ? null : languageOf(path);
  const lines = language ? splitHighlighted(highlight(text, language)) : text.split("\n").map(esc);
  return `<div class="code">${lines.map((line, index) => `
    <div class="row" data-line="${index + 1}"><span class="num">${index + 1}</span><span class="text">${line || " "}</span></div>`).join("")}
  </div>`;
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

// ---------- Diffs ----------

// A unified diff, split into files and hunks with line numbers on both sides.
export function parseDiff(patch) {
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

// Each file's hunks, one column of changes. Its name opens the file in `views`.
export function diffHtml(files, views) {
  if (!files.length) return `<p>No changes.</p>`;
  return files.map((file) => {
    const path = file.newPath || file.oldPath;
    const language = languageOf(path);
    const rows = file.hunks.map((hunk) => `
      <div class="row hunk"><span class="num"></span><span class="num"></span><span class="sign"></span><span class="text">${esc(hunk.header)}</span></div>
      ${hunk.lines.map((line) => `
        <div class="row ${line.type}">
          <span class="num">${line.oldNo ?? ""}</span><span class="num">${line.newNo ?? ""}</span>
          <span class="sign">${line.type === "add" ? "+" : line.type === "del" ? "-" : ""}</span>
          <span class="text">${highlight(line.text, language) || " "}</span>
        </div>`).join("")}`).join("");
    return `<div class="diff-file"><p class="diff-name">${diffName(file, views)}</p><div class="code diff">${rows}</div></div>`;
  }).join("");
}

function diffName(file, views) {
  const path = file.newPath || file.oldPath;
  const name = file.oldPath && file.newPath && file.oldPath !== file.newPath
    ? `${file.oldPath} renamed to ${file.newPath}`
    : !file.oldPath ? `${path} (new file)` : !file.newPath ? `${path} (deleted)` : path;
  const [view, target] = file.newPath && views.includes("golden") ? ["golden", file.newPath] : ["buggy", file.oldPath];
  return target ? fileLink(target, view, esc(name)) : esc(name);
}

// The repair's hunks for one file, buggy on the left and golden on the right.
export function splitDiffHtml(files) {
  if (!files.length) return `<p class="message">The repair does not change this file.</p>`;
  const left = [];
  const right = [];
  for (const file of files) {
    const language = languageOf(file.newPath || file.oldPath);
    const row = (type, number, text) => text === null
      ? `<div class="row empty"><span class="num"></span><span class="text"> </span></div>`
      : `<div class="row ${type}"><span class="num">${number}</span><span class="text">${highlight(text, language) || " "}</span></div>`;
    for (const hunk of file.hunks) {
      const header = `<div class="row hunk"><span class="num"></span><span class="text">${esc(hunk.header)}</span></div>`;
      left.push(header);
      right.push(header);
      // Removed and added lines pair up side by side; the shorter side is padded.
      let removed = [];
      let added = [];
      const flush = () => {
        for (let i = 0; i < Math.max(removed.length, added.length); i++) {
          left.push(removed[i] ? row("del", removed[i].oldNo, removed[i].text) : row("", "", null));
          right.push(added[i] ? row("add", added[i].newNo, added[i].text) : row("", "", null));
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

// ---------- Links to evidence ----------

// Each kind of link names what it opens; the review page follows them all.
// A diff the trace recorded as text, such as one test file version's change or an
// apply_patch body, with added and removed lines marked.
export function plainDiffHtml(diff) {
  throw new Error("not implemented");
}

// Highlighted code, or escaped text when highlight.js does not know the language.
export function code(text, language) {
  throw new Error("not implemented");
}

export function fileLink(path, view, label, line = "") {
  return `<button type="button" class="link-like" data-open-file="${esc(path)}" data-view="${view}" data-line="${line}">${label}</button>`;
}

export function testLink(testId) {
  return `<button type="button" class="link-like" data-open-test="${esc(testId)}">${esc(testId)}</button>`;
}

function evidenceLink(name, label) {
  return `<button type="button" class="link-like" data-open-evidence="${esc(name)}">${label}</button>`;
}

// Text from a judge, with backtick spans as code and workspace paths as links.
export function linkify(text) {
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
    // A trailing period or colon ends the sentence, not the path; `:12` is a line.
    const [cited, area, rest] = match;
    const trailing = rest.match(/[.:]+$/)?.[0] || "";
    const whole = cited.slice(0, cited.length - trailing.length);
    const [, path, line] = rest.slice(0, rest.length - trailing.length).match(/^(.*?)(?::(\d+)(?::\d+)?)?$/);
    html += esc(text.slice(start, match.index));
    html += area === "buggy" || area === "golden"
      ? fileLink(path, area, esc(whole), line || "")
      : evidenceLink(area ? `${area}/${path}` : path, esc(whole));
    start = match.index + whole.length;
  }
  return html + esc(text.slice(start));
}
