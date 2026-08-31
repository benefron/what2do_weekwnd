# what2do_weekwnd

A personal weekend dashboard for finding things to do with the kids (a 4-year-old
and an 8-year-old, Dutch speakers, no French) in and around Leuven.

A local Python pipeline runs every Monday morning: it pulls family events from the
Flemish agenda ecosystem + a set of venue scrapers, geocodes them, has Claude
classify each one (type, features, age fit, language, price), and writes
`data/latest.json` into the repo. A GitHub Actions workflow then builds the
Vite/React PWA and deploys it to GitHub Pages. Same shape as
[israel-news-digest](https://github.com/benefron/israel-news-digest).

## Layout

```
automation/     Python pipeline (runs locally via launchd, NOT in CI)
data/           Generated JSON committed to the repo (latest.json + archive)
frontend/       Vite + React + Tailwind PWA (built & deployed by CI)
scripts/        launchd install + dev/test helpers
```

## First-time setup

```bash
python3 -m venv automation/.venv
automation/.venv/bin/pip install -r automation/requirements.txt
cp automation/secrets.local.json.example automation/secrets.local.json   # optional
cd frontend && npm install && cd ..

git remote add origin git@github.com:benefron/what2do_weekwnd.git
git config credential.helper osxkeychain        # for non-interactive push from launchd
```

Then on GitHub: create the public repo, push, and set **Settings → Pages →
Source: GitHub Actions**.

## Running the pipeline

```bash
scripts/verify_sources.sh              # ping every source, see what's alive
scripts/run_now.sh --no-push           # full run, write data files, don't commit
scripts/run_now.sh --no-push --no-enrich   # skip the Claude step (fast, offline)
scripts/run_now.sh                     # full run + commit + push (triggers deploy)
```

`run_weekly.py` guards: a file lock, a ~20h idempotency window, and it aborts
without overwriting `latest.json` if zero activities come back.

## Scheduling (weekly, Monday 07:30)

```bash
scripts/install_launchd.sh            # installs ~/Library/LaunchAgents/com.benefron.weekwnd.plist
scripts/uninstall_launchd.sh
launchctl kickstart -k gui/$(id -u)/com.benefron.weekwnd   # force a run now
```

`RunAtLoad` is off on purpose — launchd runs a missed Monday slot on the next
wake, so a Mac that was off on Monday still runs the pipeline when it wakes.

## Frontend

```bash
scripts/dev_serve.sh                  # copies data/latest.json in, runs vite dev
cd frontend && npm run build          # -> frontend/dist
```

UI is English; activity titles/descriptions stay in Dutch with a per-card
**Translate** link to Google Translate. Two tabs: dated events ("This weekend &
beyond") and permanent venues ("Places to go"). All filtering is client-side and
synced to the URL.

## Data sources

v1 is free-only: the UiTinVlaanderen agenda RSS (the same database behind
`leuven.be`, the library, tourism and most museums) plus JSON-LD scrapers for
individual venues, plus `data/manual_overrides.json` for permanent venues and
seasonal farm activities.

The scraper URLs in `automation/config.py` need live verification — run
`scripts/verify_sources.sh` and fix any that 404. See
`automation/uitdatabank_client.py` for enabling the structured UiTdatabank
Search API later (needs a publiq key).
