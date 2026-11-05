# Rater guide

You need Git with access to both repositories, [uv](https://docs.astral.sh/uv/),
and a web browser. You do not need Docker or an API key.

Replace `<your-id>` everywhere with your rater ID: letters, digits, `-`, or `_`,
such as `alice`. Use the same ID every time.

## 1. Set up (once)

```sh
# Download the Oracle Lab code.
git clone <oracle-lab-url> oracle-lab

# Download the data repository with the runs to review, beside it.
git clone <data-repository-url> oracle-lab-data

# Go into the Oracle Lab folder. Run every later command from here.
cd oracle-lab

# Install Oracle Lab.
uv sync

# Create your own branch in the data repository. Your reviews are saved there.
git -C ../oracle-lab-data switch -c reviews/<your-id>
```

## 2. Review

Run this from the `oracle-lab` folder.

```sh
# Open the review page in your browser. Press Ctrl+C here to stop it.
uv run oracle-lab review ../oracle-lab-data --rater <your-id>
```

Work one problem at a time: its classification first, then each run. Read the
"How to review" dialog on your first rating page. You can stop and resume at any
time.

## 3. Hand in your reviews

Do this whenever you want to save your progress, and when you finish.

```sh
# Stage your saved reviews.
git -C ../oracle-lab-data add -A

# Record them in a commit.
git -C ../oracle-lab-data commit -m "Reviews by <your-id>"

# Upload your branch.
git -C ../oracle-lab-data push -u origin reviews/<your-id>
```

## 4. Get new runs or updates

Do this when the maintainer tells you there are new runs, or a new version of
Oracle Lab. Stop the review page first.

```sh
# Download the maintainer's new runs.
git -C ../oracle-lab-data fetch origin

# Add them to your branch.
git -C ../oracle-lab-data merge --no-edit origin/main

# Download the newest Oracle Lab code.
git pull

# Install its dependencies.
uv sync
```

## If something goes wrong

```sh
# The port is already in use: start the page on another port.
uv run oracle-lab review ../oracle-lab-data --rater <your-id> --port 8800
```

If the browser does not open, open the URL the command printed. For anything
else, contact the maintainer.
