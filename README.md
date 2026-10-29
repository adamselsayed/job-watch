# job-watch

A small, self-hosted job-opening tracker. Every morning a GitHub Action pulls the
job boards of the companies in `companies.yml`, keeps the roles that match the watch
criteria, records what's new and what closed, and publishes a dashboard on GitHub Pages.

## How it fits together

```
companies.yml            <- the watchlist (you edit this)
scripts/fetch_jobs.py    <- pulls boards, filters, diffs against the last snapshot
data/jobs.json           <- the snapshot the dashboard reads (written by the script)
data/changes.log         <- human-readable history of NEW / CLOSED / REOPENED events
index.html               <- the dashboard (GitHub Pages serves it)
.github/workflows/daily.yml  <- runs the script at 6 AM Pacific and commits the result
```

## Watch criteria (edit in `scripts/fetch_jobs.py` **and** `index.html`)

- Title contains **"product"** and does not contain the standalone word **"design"** (so "Product Designer" passes, "Product Design" does not)
- Location is in the **United States** (postings that don't state a country are kept and flagged *verify US*)
- Each role shows: remote / hybrid / on-site (flagged when inferred or unknown), posted date & time,
  salary range when the posting includes one, and the requisition ID / job ID.

## Adding a company

1. Find its job-board system and token (see the comments in `companies.yml`).
2. Add a block to `companies.yml`.
3. Run `python3 scripts/fetch_jobs.py` locally, or wait for the next morning's sweep, or trigger
   **Actions → Daily job sweep → Run workflow** on GitHub.

Supported boards: Greenhouse, Lever, Ashby. Companies on Workday or custom career sites need a
new adapter in `fetch_jobs.py` (a function that returns the same normalized dicts).

## Running locally

```bash
python3 scripts/fetch_jobs.py            # all companies
python3 scripts/fetch_jobs.py Samsara    # one company
python3 -m http.server 8000              # then open http://localhost:8000
```

The script uses only the Python standard library (3.9+).

## "Pull now"

The dashboard's **Pull now** button reads the company's live job board directly from your browser
and shows anything the morning sweep hasn't recorded yet with a *live* badge. It doesn't write to
the repo; the next sweep will record those roles permanently.
