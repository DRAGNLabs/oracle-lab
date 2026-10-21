// The question rail: each answer the judge gave, the rater's rating of it, saving,
// and Next task once everything is saved.

import { nextTask } from "./listing.js";
import { esc, linkify } from "./render.js";
import { api, state } from "./state.js";

export const RATINGS = ["correct", "defensible", "incorrect"];
export const RATING_NAMES = { correct: "Correct", defensible: "Defensible", incorrect: "Incorrect" };

// Start rating the open target from where the rater left off.
export function startRail(target) {
  state.ratings = readDraft(target) || { ...(target.review || {}) };
  state.selected = Math.max(0, target.questions.findIndex((q) => !state.ratings[q.key]));
  document.getElementById("questions").addEventListener("click", (event) => {
    const card = event.target.closest(".question");
    if (!card) return;
    const button = event.target.closest("[data-rating]");
    if (button) rate(Number(card.dataset.index), button.dataset.rating);
    else select(Number(card.dataset.index));
  });
  renderQuestions();
  renderFoot();
}

export function select(index) {
  state.selected = Math.min(Math.max(index, 0), state.target.questions.length - 1);
  renderQuestions();
  document.querySelector(".question.selected")?.scrollIntoView({ block: "nearest" });
}

// Rate one question. From the keyboard, move on to the next unrated question.
export function rate(index, rating, { advance = false } = {}) {
  const questions = state.target.questions;
  state.ratings[questions[index].key] = rating;
  writeDraft();
  const unrated = questions.findIndex((q, i) => i > index && !state.ratings[q.key]);
  select(advance && unrated !== -1 ? unrated : index);
  renderFoot();
}

function renderQuestions() {
  const { target, ratings, selected } = state;
  const definitions = state.listing.rating_definitions;
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
              aria-checked="${rating === key}" data-tip="${esc(definitions[key])}">
              <kbd>${n + 1}</kbd>${RATING_NAMES[key]}
            </button>`).join("")}
        </div>
      </li>`;
  }).join("");
}

// Progress, the save button, and after saving, the way to the next target.
function renderFoot({ justSaved = false, error = "" } = {}) {
  const { target, ratings } = state;
  const total = target.questions.length;
  const rated = target.questions.filter((q) => ratings[q.key]).length;
  const complete = rated === total;
  const saved = Boolean(target.review) && sameRatings(ratings, target.review);

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
  const { target, ratings } = state;
  try {
    const result = await api("/api/review", { id: target.id }, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ratings }),
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

function sameRatings(a, b) {
  const left = a || {};
  const right = b || {};
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  return [...keys].every((key) => left[key] === right[key]);
}

// ---------- Drafts: unsaved ratings live in this browser until saved ----------

function draftKey(id) {
  return `oracle-review:${state.listing.rater}:${id}`;
}

// The unsaved ratings of one task, kept only for questions it still has.
export function draftRatings(id, questionKeys) {
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey(id)) || "null");
    if (!draft) return {};
    const keys = new Set(questionKeys);
    return Object.fromEntries(
      Object.entries(draft).filter(([key, value]) => keys.has(key) && RATINGS.includes(value)),
    );
  } catch {
    return {};
  }
}

function readDraft(target) {
  const draft = draftRatings(target.id, target.questions.map((q) => q.key));
  return Object.keys(draft).length ? draft : null;
}

function writeDraft() {
  try {
    if (sameRatings(state.ratings, state.target.review)) clearDraft();
    else localStorage.setItem(draftKey(state.target.id), JSON.stringify(state.ratings));
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
