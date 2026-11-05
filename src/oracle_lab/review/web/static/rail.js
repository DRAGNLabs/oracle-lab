// The question rail: each answer and failure-mode decision the judge gave, the
// rater's agree or disagree and why for each disagree, saving, and Next task once
// everything is saved. Sections: Submission (or the classification's questions)
// and Failure modes (present labels as cards, absent as one-line rows, then the
// judge's own labels).

import { nextTask } from "./listing.js";
import { esc, linkify } from "./render.js";
import { api, state } from "./state.js";

export const RATINGS = ["agree", "disagree"];
export const RATING_NAMES = { agree: "Agree", disagree: "Disagree" };

// Display order, which is also the order `j`/`k` and `1`/`2` move through.
const SECTION_ORDER = { submission: 0, failure_modes: 1, added_failure_modes: 2 };

// Start rating the open target from where the rater left off.
export function startRail(target) {
  target.questions.sort((a, b) => rank(a) - rank(b));
  const saved = target.review || { ratings: {}, justifications: {} };
  const draft = readDraft(target);
  state.ratings = draft?.ratings || { ...saved.ratings };
  state.justifications = draft?.justifications || { ...saved.justifications };
  state.selected = Math.max(0, target.questions.findIndex((q) => !state.ratings[q.key]));
  const rail = document.getElementById("questions");
  rail.addEventListener("click", (event) => {
    if (event.target.closest("[data-open-step], textarea")) return; // followed by the page, or typing
    const card = event.target.closest(".question");
    if (!card) return;
    const button = event.target.closest("[data-rating]");
    if (button) rate(Number(card.dataset.index), button.dataset.rating);
    else select(Number(card.dataset.index));
  });
  // Typing updates the review without redrawing the rail, so the box keeps focus.
  rail.addEventListener("input", (event) => {
    const box = event.target.closest("textarea[data-why]");
    if (!box) return;
    state.justifications[box.dataset.why] = box.value;
    writeDraft();
    renderFoot();
  });
  renderQuestions();
  renderFoot();
}

// Present start labels before absent ones, so the cards come first.
function rank(item) {
  const absent = item.section === "failure_modes" && item.answer === "absent" ? 0.5 : 0;
  return SECTION_ORDER[item.section] + absent;
}

export function select(index) {
  state.selected = Math.min(Math.max(index, 0), state.target.questions.length - 1);
  renderQuestions();
  document.querySelector(".question.selected")?.scrollIntoView({ block: "nearest" });
}

// Rate one item. A disagree asks why; from the keyboard, an agree moves on to the
// next unrated item.
export function rate(index, rating, { advance = false } = {}) {
  const questions = state.target.questions;
  const key = questions[index].key;
  state.ratings[key] = rating;
  if (rating === "disagree") state.justifications[key] ??= "";
  else delete state.justifications[key];
  writeDraft();
  const unrated = questions.findIndex((q, i) => i > index && !state.ratings[q.key]);
  select(advance && rating === "agree" && unrated !== -1 ? unrated : index);
  if (rating === "disagree") document.querySelector(`textarea[data-why="${key}"]`)?.focus();
  renderFoot();
}

function renderQuestions() {
  const { target } = state;
  const items = target.questions.map((item, index) => ({ item, index }));
  const of = (test) => items.filter(({ item }) => test(item)).map(({ item, index }) => itemHtml(item, index)).join("");
  const judgment = target.stage === "judge";
  const present = of((item) => item.section === "failure_modes" && item.answer === "present");
  const absent = of((item) => item.section === "failure_modes" && item.answer === "absent");
  const added = of((item) => item.section === "added_failure_modes");
  document.getElementById("questions").innerHTML = `
    ${judgment ? `<li class="rail-section"><h2>Submission</h2></li>` : ""}
    ${of((item) => item.section === "submission")}
    ${judgment ? `
      <li class="rail-section"><h2>Failure modes</h2>
        <p>The judge decided each start-list label. Agree or disagree with each decision.</p></li>
      ${present || `<li class="rail-note">The judge found none of the start-list labels.</li>`}
      ${absent ? `<li class="rail-subhead">Absent</li>${absent}` : ""}
      ${added ? `<li class="rail-subhead">The judge's own labels</li>${added}` : ""}` : ""}`;
}

// One item: a full card for a question, a present failure mode, or a judge-added
// one; a one-line row for an absent start label. A disagree adds the reason box.
function itemHtml(item, index) {
  const rating = state.ratings[item.key];
  const classes = `question ${index === state.selected ? "selected" : ""} ${rating ? `rated-${rating}` : ""}`;
  const why = rating === "disagree" ? whyBox(item.key) : "";
  if (item.section === "failure_modes" && item.answer === "absent") {
    return `
      <li class="${classes} compact" data-index="${index}">
        <div class="compact-row"><span class="compact-label">${esc(item.label)}</span>${rateButtons(rating, true)}</div>
        ${why}
      </li>`;
  }
  const answers = Array.isArray(item.answer) ? item.answer : [item.answer];
  const intro = item.section === "submission" ? "Judge answered" : "Judge found it";
  return `
    <li class="${classes}" data-index="${index}">
      <h2>${esc(item.label)}</h2>
      ${item.definition ? `<p class="definition">${esc(item.definition)}</p>` : ""}
      <div class="answer">
        <span class="answer-intro">${intro}</span>
        ${answers.map((answer) => `<span class="answer-value">${esc(answer)}</span>`).join("")}
        ${item.steps ? `<span class="answer-intro">at ${stepLinks(item.steps)}</span>` : ""}
      </div>
      <p class="justification">${linkify(item.justification)}</p>
      ${rateButtons(rating)}
      ${why}
    </li>`;
}

function whyBox(key) {
  return `
    <label class="why">Why you disagree (required)
      <textarea data-why="${esc(key)}" rows="3" maxlength="2000" placeholder="What the right answer is, and why">${esc(state.justifications[key] || "")}</textarea>
    </label>`;
}

function rateButtons(rating, compact = false) {
  const definitions = state.listing.rating_definitions;
  return `
    <div class="rate ${compact ? "rate-compact" : ""}" role="radiogroup" aria-label="Your rating">
      ${RATINGS.map((key, n) => `
        <button type="button" role="radio" class="rate-${key}" data-rating="${key}"
          aria-checked="${rating === key}" data-tip="${esc(definitions[key])}">
          ${compact ? "" : `<kbd>${n + 1}</kbd>`}${RATING_NAMES[key]}
        </button>`).join("")}
    </div>`;
}

function stepLinks(steps) {
  const links = steps.map((step) => `<button type="button" class="link-like" data-open-step="${step}">${step}</button>`);
  return `step${steps.length > 1 ? "s" : ""} ${links.join(", ")}`;
}

// The review as it would be saved: a justification for each disagree, trimmed.
function currentReview() {
  const justifications = Object.fromEntries(
    Object.entries(state.ratings)
      .filter(([, rating]) => rating === "disagree")
      .map(([key]) => [key, (state.justifications[key] || "").trim()]),
  );
  return { ratings: state.ratings, justifications };
}

// Progress, the save button, and after saving, the way to the next task.
function renderFoot({ justSaved = false, error = "" } = {}) {
  const { target, ratings } = state;
  const total = target.questions.length;
  const rated = target.questions.filter((q) => ratings[q.key]).length;
  const unexplained = Object.values(currentReview().justifications).filter((text) => !text).length;
  const complete = rated === total && !unexplained;
  const saved = Boolean(target.review) && sameReview(currentReview(), target.review);

  let status = `${rated} of ${total} rated`;
  if (rated === total && unexplained) status = `${unexplained} disagree${unexplained === 1 ? "" : "s"} still need${unexplained === 1 ? "s" : ""} a reason`;
  if (saved) status = "Saved";
  else if (complete) status = target.review ? "Changed, not saved yet" : "All rated, not saved yet";

  let nextLink = "";
  if (saved) {
    const next = nextTask(target.id);
    nextLink = next
      ? `<a class="next-link" href="#/review/${encodeURIComponent(next.id)}">Next task</a>`
      : `<a class="next-link" href="#/">Every task is reviewed</a>`;
  }

  document.getElementById("rail-foot").innerHTML = `
    <p class="save-status ${saved ? "saved" : ""}">${status}</p>
    <button type="button" class="primary" id="save" ${complete && !saved ? "" : "disabled"}>
      ${target.review ? "Save changes" : "Save review"}
    </button>
    <p class="save-error" role="alert">${esc(error)}</p>
    ${nextLink}`;
  document.getElementById("save").addEventListener("click", saveReview);
  if (justSaved) document.querySelector(".next-link")?.focus();
}

async function saveReview() {
  const { target } = state;
  try {
    const result = await api("/api/review", { id: target.id }, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentReview()),
    });
    target.review = result.review;
    clearDraft();
    const entry = state.listing.problems.flatMap((problem) => problem.tasks).find((item) => item.id === target.id);
    if (entry) entry.reviewed = true;
    renderFoot({ justSaved: true });
  } catch (error) {
    renderFoot({ error: error.message });
  }
}

function sameReview(a, b) {
  const same = (x, y) => {
    const keys = new Set([...Object.keys(x || {}), ...Object.keys(y || {})]);
    return [...keys].every((key) => (x || {})[key] === (y || {})[key]);
  };
  return same(a?.ratings, b?.ratings) && same(a?.justifications, b?.justifications);
}

// ---------- Drafts: an unsaved review lives in this browser until saved ----------

function draftKey(id) {
  return `oracle-review:${state.listing.rater}:${id}`;
}

function loadDraft(id) {
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey(id)) || "null");
    return draft && typeof draft.ratings === "object" ? draft : null;
  } catch {
    return null;
  }
}

// The unsaved ratings of one task, kept only for items it still has.
export function draftRatings(id, keys) {
  const known = new Set(keys);
  return Object.fromEntries(
    Object.entries(loadDraft(id)?.ratings || {}).filter(([key, value]) => known.has(key) && RATINGS.includes(value)),
  );
}

function readDraft(target) {
  const draft = loadDraft(target.id);
  if (!draft) return null;
  const ratings = draftRatings(target.id, target.questions.map((q) => q.key));
  const justifications = typeof draft.justifications === "object" && draft.justifications ? draft.justifications : {};
  return {
    ratings,
    justifications: Object.fromEntries(Object.keys(ratings).filter((key) => ratings[key] === "disagree").map((key) => [key, String(justifications[key] || "")])),
  };
}

function writeDraft() {
  try {
    if (sameReview(currentReview(), state.target.review)) clearDraft();
    else localStorage.setItem(draftKey(state.target.id), JSON.stringify(currentReview()));
  } catch {
    // Without storage, ratings still work until the tab closes.
  }
}

function clearDraft() {
  try {
    localStorage.removeItem(draftKey(state.target.id));
  } catch {
    // Nothing was stored.
  }
}
