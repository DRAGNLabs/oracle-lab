#!/usr/bin/env bash
# Gather every rater's reviews onto a local `agreement` branch of the data repository.
#
# Each rater's branch holds only their assigned problems, so the branches cannot be
# merged. Instead, `agreement` starts from `main` (every problem) and copies in only
# each rater's own review files. Run from oracle-lab, then compute agreement:
#
#   experiments/human-review/collect.sh ../oracle-lab-data-v2
#   uv run oracle-lab agreement ../oracle-lab-data-v2
#
# The `agreement` branch is local only: never push it.
set -euo pipefail
data="$1"
raters=$(python3 -c "import json, sys; print(' '.join(json.load(open(sys.argv[1]))['own']))" \
  "$(dirname "$0")/assignment.json")

git -C "$data" diff --quiet && git -C "$data" diff --cached --quiet \
  || { echo "$data has uncommitted changes; commit or stash them first." >&2; exit 1; }
git -C "$data" fetch -q origin
git -C "$data" switch -q -C agreement origin/main
for rater in $raters; do
  branch="origin/reviews/$rater"
  if ! git -C "$data" rev-parse -q --verify "$branch" >/dev/null; then
    echo "$rater: no branch $branch" >&2
    continue
  fi
  mapfile -t files < <(git -C "$data" ls-tree -r --name-only "$branch" \
    | grep -E "/judge/reviews(-history)?/$rater/" || true)
  if [ "${#files[@]}" -gt 0 ]; then
    git -C "$data" checkout -q "$branch" -- "${files[@]}"
  fi
  echo "$rater: ${#files[@]} review files"
done
git -C "$data" commit -q -m "Collect reviews" || echo "No reviews to collect."
