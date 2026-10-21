// The problem view: what a rater reads before rating anything about a task. It is
// both the problem page, between the home page and the problem's first task, and
// the Problem tab of every rating page.

import { api, state } from "./state.js";

// The problem view into `container`: the instance, the issue, the record's other
// texts, the golden patch, and the reference tests.
export function renderProblem(container, problem) {
  throw new Error("not implemented");
}

// The problem page: the problem view, its tasks with this rater's progress, and
// "Start rating", which opens its first unrated task.
export async function openProblem(instanceId) {
  throw new Error("not implemented");
}
