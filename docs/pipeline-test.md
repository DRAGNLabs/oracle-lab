# Full pipeline test

Every command to test the whole pipeline end to end, using the validation batch:
`pytest-dev__pytest-10051` with Claude Code, `wtforms__wtforms-809` with Codex,
and `scikit-learn__scikit-learn-15100` with OpenCode. Run them in order from
the `oracle-lab` repository root.

Steps 3 and 4 call models and spend money on the OpenRouter key. Steps 5 to 8
use a private GitHub repository and need `gh` logged in with push access.

## 1. Install

```sh
uv sync --extra dev
[ -f .env ] || cp .env.example .env   # then set OPENROUTER_API_KEY in .env
docker info
```

## 2. Local checks

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

## 3. Set up the problems

Preflights each problem: builds its image, computes its target and fix scope, and
runs the reference and environment checks. Then classifies it. All three must
end `cleared`. If one fails, fix it and run the same command again; only the
unfinished steps run.

```sh
uv run oracle-lab setup configs/problems/validation.yaml
cat output/setup/validation.md
```

## 4. Batch

Runs the three jobs, each with generation, evaluation, the LLM judge, and a
report.

```sh
uv run oracle-lab batch configs/validation/batch.yaml
```

If it stops partway, continue it:

```sh
uv run oracle-lab batch --resume output/batches/<batch-id>
```

Read the summary, then each run's report:

```sh
cat output/batches/<batch-id>/report.md
cat output/runs/<run-id>/report.md
```

## 5. Publish the review set

Creates a private test repository under your GitHub account, beside
`oracle-lab`, and publishes the runs and classifications on `main`.

```sh
cd ..
gh repo create oracle-lab-data-test --private --clone
cp -r oracle-lab/output/runs oracle-lab/output/classifications oracle-lab-data-test/

cd oracle-lab-data-test
git add runs classifications
git commit -m "Add review set"
git branch -M main
git push -u origin main
cd ../oracle-lab
```

## 6. Review

Two raters, each with their own clone and branch. Rate a few items, then stop
the page with Ctrl+C.

```sh
gh repo clone oracle-lab-data-test ../data-tester-a
git -C ../data-tester-a switch -c reviews/tester-a
uv run oracle-lab review ../data-tester-a --rater tester-a
git -C ../data-tester-a add -A
git -C ../data-tester-a commit -m "Reviews by tester-a"
git -C ../data-tester-a push -u origin reviews/tester-a
```

```sh
gh repo clone oracle-lab-data-test ../data-tester-b
git -C ../data-tester-b switch -c reviews/tester-b
uv run oracle-lab review ../data-tester-b --rater tester-b
git -C ../data-tester-b add -A
git -C ../data-tester-b commit -m "Reviews by tester-b"
git -C ../data-tester-b push -u origin reviews/tester-b
```

## 7. Agreement

Merge both rater branches into a local `agreement` branch. Never push it.

```sh
cd ../oracle-lab-data-test
git fetch
git switch -c agreement origin/main
git merge origin/reviews/tester-a origin/reviews/tester-b
cd ../oracle-lab

uv run oracle-lab agreement ../oracle-lab-data-test
```

## 8. Export

```sh
uv run oracle-lab export ../oracle-lab-data-test --out output/dataset-test
```

## Clean up

```sh
rm -rf ../oracle-lab-data-test ../data-tester-a ../data-tester-b output/dataset-test
gh repo delete oracle-lab-data-test --yes   # needs: gh auth refresh -s delete_repo
```
