// The question rail: each answer and failure-mode decision the judge gave, the
// rater's agree or disagree, the rater's own failure-mode labels, saving, and Next
// task once everything is saved. Sections: Submission (or the classification's
// questions), Failure modes (present labels as cards, absent as one-line rows), and
// Your other labels.

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
  const saved = target.review || { ratings: {}, other_failure_modes: [] };
  const draft = readDraft(target);
  state.ratings = draft?.ratings || { ...saved.ratings };
  state.otherLabels = draft?.other_failure_modes || [...saved.other_failure_modes];
  state.selected = Math.max(0, target.questions.findIndex((q) => !state.ratings[q.key]));
  const rail = document.getElementById("questions");
  rail.addEventListener("click", (event) => {
    if (event.target.closest("[data-open-step]")) return; // a step link, followed by the page
    const remove = event.target.closest("[data-remove-label]");
    if (remove) return removeLabel(Number(remove.dataset.removeLabel));
    const card = event.target.closest(".question");
    if (!card) return;
    const button = event.target.closest("[data-rating]");
    if (button) rate(Number(card.dataset.index), button.dataset.rating);
    else select(Number(card.dataset.index));
  });
  rail.addEventListener("submit", addLabel);
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

// Rate one item. From the keyboard, move on to the next unrated item.
export function rate(index, rating, { advance = false } = {}) {
  const questions = state.target.questions;
  state.ratings[questions[index].key] = rating;
  writeDraft();
  const unrated = questions.findIndex((q, i) => i > index && !state.ratings[q.key]);
  select(advance && unrated !== -1 ? unrated : index);
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
      ${added ? `<li class="rail-subhead">The judge's own labels</li>${added}` : ""}
      ${renderOtherLabels()}` : ""}`;
}

// One item: a full card for a question, a present failure mode, or a judge-added
// one; a one-line row for an absent start label.
function itemHtml(item, index) {
  const rating = state.ratings[item.key];
  const classes = `question ${index === state.selected ? "selected" : ""} ${rating ? `rated-${rating}` : ""}`;
  if (item.section === "failure_modes" && item.answer === "absent") {
    return `
      <li class="${classes} compact" data-index="${index}">
        <span class="compact-label">${esc(item.label)}</span>${rateButtons(rating, true)}
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
    </li>`;
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

// "Your other labels": the rater's own failure modes, and the form that adds one.
function renderOtherLabels() {
  const labels = state.otherLabels.map((label, index) => `
    <li class="other-label">
      <div><b>${esc(label.name)}</b> · ${stepLinks(label.steps)}</div>
      <p>${esc(label.description)}</p>
      <button type="button" class="link-like" data-remove-label="${index}">Remove</button>
    </li>`).join("");
  return `
    <li class="rail-section"><h2>Your other labels</h2>
      <p>Optional. Failure modes you see that none of the start-list labels describes.</p></li>
    ${labels}
    <li class="other-label-form">
      <form id="other-label-form">
        <input name="name" placeholder="Name, such as Tested only defaults" maxlength="80" required>
        <textarea name="description" placeholder="What the agent did, in one sentence" maxlength="500" rows="2" required></textarea>
        <input name="steps" placeholder="Steps, such as 12, 14" pattern="\\s*\\d+(\\s*,\\s*\\d+)*\\s*" required>
        <button type="submit" class="link-button">Add label</button>
      </form>
    </li>`;
}

function addLabel(event) {
  event.preventDefault();
  const form = event.target;
  state.otherLabels.push({
    name: form.name.value.trim(),
    description: form.description.value.trim(),
    steps: form.steps.value.split(",").map((step) => Number(step.trim())),
  });
  writeDraft();
  renderQuestions();
  renderFoot();
}

function removeLabel(index) {
  state.otherLabels.splice(index, 1);
  writeDraft();
  renderQuestions();
  renderFoot();
}

// The review as it would be saved.
function currentReview() {
  return { ratings: state.ratings, other_failure_modes: state.otherLabels };
}

// Progress, the save button, and after saving, the way to the next task.
function renderFoot({ justSaved = false, error = "" } = {}) {
  const { target, ratings } = state;
  const total = target.questions.length;
  const rated = target.questions.filter((q) => ratings[q.key]).length;
  const complete = rated === total;
  const saved = Boolean(target.review) && sameReview(currentReview(), target.review);

  let status = `${rated} of ${total} rated`;
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
  const left = a?.ratings || {};
  const right = b?.ratings || {};
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  return [...keys].every((key) => left[key] === right[key])
    && JSON.stringify(a?.other_failure_modes || []) === JSON.stringify(b?.other_failure_modes || []);
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
  const labels = Array.isArray(draft.other_failure_modes) ? draft.other_failure_modes : [];
  return { ratings: draftRatings(target.id, target.questions.map((q) => q.key)), other_failure_modes: labels };
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
