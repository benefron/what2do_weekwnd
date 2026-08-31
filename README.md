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

## Two data tiers

- **`data/latest.json`** — the weekly events feed. Refreshed every Monday by
  `run_weekly.py`. The enrichment cache means only new/changed events cost Claude
  tokens.
- **`data/places.json`** — the permanent guide (museums, zoos, provincial
  domains, speelbossen, playgrounds, attraction parks, zomerbars,
  playground-restaurants across Belgium). **Not re-fetched weekly** — the weekly
  run just merges it in. Rebuild/expand it manually:

  ```bash
  scripts/build_places.sh                       # all kinds (~$10 web search)
  scripts/build_places.sh --kinds zomerbar,speelbos
  ```

  `source: "curated"` entries are never overwritten — hand-edit those freely.

## Running the weekly pipeline

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
**Translate** link to Google Translate. Four tabs — *This weekend & beyond*
(dated events), *Places to go*, *Zomerbars*, *Eat & play* (playground-restaurants).
All filtering is client-side and synced to the URL.

## Data sources

Free, no API key:

1. **UiT agendas** — scrape `uitinleuven.be/agenda` (city) and
   `uitinvlaanderen.be/agenda` (all Flanders) listing pages for event UUIDs,
   hydrate each via the public `https://io.uitdatabank.be/events/<uuid>` endpoint
   (same database behind `leuven.be`, the library, tourism and most museums).
2. **Claude web search** — a Sonnet pass with web search that finds big-name
   concerts, touring musicals and major shows anywhere in Belgium that the local
   agendas under-cover.
3. `data/places.json` — the permanent guide, built separately (see above).
4. `data/manual_overrides.json` — one-off event corrections.

Filters before publishing: a kid-relevance prefilter (age / family terms) on the
agenda scrape, a distance prefilter (`MAX_DISTANCE_KM`), and a Claude
`family_relevant` check that drops adult-only films / courses / nightlife while
keeping kid activities and big-name concerts. `automation/uitdatabank_client.py`
is the paid UiTdatabank Search API for real server-side region filtering later.
