# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal weekend-activities dashboard for a family in Leuven (kids aged 4 and 8,
Dutch speakers, no French). A local Python pipeline scrapes/enriches family events
and writes JSON into the repo; GitHub Actions only builds and deploys the frontend.
Modelled on `../israel-news-digest` — same "local pipeline commits data, CI just
deploys" architecture.

## Commands

```bash
# Pipeline (from repo root)
scripts/verify_sources.sh                    # ping every source; run this before trusting a change
scripts/run_now.sh --no-push --no-enrich     # fast offline run (no Claude), writes data/latest.json
scripts/run_now.sh --no-push                 # full run incl. Claude enrichment, no commit
scripts/run_now.sh                           # full run + commit + push (push triggers Pages deploy)
automation/.venv/bin/python automation/run_weekly.py --force --no-push   # same as run_now.sh

# Single stage in isolation (python -c against the module) — every stage is importable & pure:
#   sources.fetch_all() -> normalize.normalize_all(raw, run_id) -> geo.geocode_activities(acts)
#   -> enrich.enrich_all(acts) -> publish.build_payload(...) / publish.write_latest(...)

# Frontend
cd frontend && npm install
npm run dev                                   # or: scripts/dev_serve.sh (syncs data/latest.json first)
npm run build                                 # tsc + vite build -> frontend/dist

# Scheduling
scripts/install_launchd.sh                     # weekly Monday 07:30 LaunchAgent
launchctl kickstart -k gui/$(id -u)/com.benefron.weekwnd
```

No test suite yet. `automation/.venv/bin/python -m py_compile automation/*.py` is the
current smoke check for the pipeline; `npm run build` type-checks the frontend.

## Pipeline architecture (`automation/`)

`run_weekly.py` orchestrates five stages, each a standalone module. Guards copied
from israel-news-digest's `run_daily.py`: file lock (`state/run.lock`),
`MIN_HOURS_BETWEEN_RUNS` (~20h) idempotency window, and **abort without
overwriting `data/latest.json`** if a stage yields nothing.

1. **`sources.py` — fetch.** Produces a flat list of raw records, each tagged
   `_kind` = `uitdatabank` | `manual`. v1 path (`fetch_uit_agenda`): scrape the
   server-rendered `uitinleuven.be/agenda` listing pages (`?page=0..N`, capped by
   `UIT_AGENDA_MAX_PAGES`) for `/agenda/e/<slug>/<uuid>` links, then hydrate each
   via the **public, no-auth** endpoint `https://io.uitdatabank.be/events/<uuid>`
   — full UiTdatabank JSON-LD (`name`/`description` multilingual, `typicalAgeRange`
   as `"6-11"`, `priceInfo[]`, `calendarType`+`startDate`/`endDate`/`subEvent`,
   `terms`, `location.geo`, `languages`). This is *not* schema.org shape.
   `http_get()` retries a 403 via `tls_client`. Plus `data/manual_overrides.json`.
   **A dead source logs and is skipped — never aborts.** `config.UITDATABANK_ENABLED`
   (auto-true when publiq creds are in `secrets.local.json`) additionally pulls
   `uitdatabank_client.py` (the paid Search API) as a structured primary source.

2. **`normalize.py` — raw → partial `Activity`, prefilter, dedupe.**
   `_from_uitdatabank` / `_from_manual` map each `_kind`.
   `id = sha1(canonicalize_url(public_url))[:10]`. Parses `calendarType`/`subEvent`
   into `occurrences[]` + `date_kind` (`single|multi_day|recurring|permanent`);
   computes `weekend_bucket` (`this_weekend|next_weekend|school_holiday|later`,
   including span-overlap for multi-day runs) and `in_school_holiday` from
   `config.SCHOOL_HOLIDAYS` (hardcoded Flemish table — **update yearly**).
   **Kid-relevance prefilter** (`_is_kid_relevant`): an event survives to the
   Claude step only if `age_min <= config.PREFILTER_MAX_AGE_MIN` OR it carries a
   family/kids term/label OR its text matches a `config.KID_KEYWORDS` entry.
   Drops past-only, adults-only (`age_min >= 16`), and (non-permanent) events with
   no bucket in the horizon. Cross-source dedupe: same `id` OR fuzzy-title ≥ 0.9 +
   same `date_start` + same city; keeps the record with more structure.

3. **`geo.py` — geocode + distance.** Prefers payload lat/lng; else Nominatim
   (1.1s throttle, custom UA) with a **git-committed** disk cache
   `automation/cache/geocode.json` (never expires). `distance_km` = haversine
   from `config.LEUVEN_CENTER`. Ungeocoded → `distance_km = null`.

4. **`enrich.py` — Claude classification.** Via `llm_runner.run_with_schema`
   (Claude CLI `--safe-mode` + JSON schema; GitHub Copilot API fallback — ported
   from israel-news-digest). Haiku bulk pass, batched `ENRICH_BATCH_SIZE`,
   scratch files in `state/enrich_batch_*.json`. **Skip-unchanged cache**
   `data/enrichment_cache.json` keyed by `id + hash(title+description+date_start)`
   → steady-state runs make ~1–3 calls. Records flagged `confidence: low` or
   caught by `_rule_conflict` (e.g. "€" in text but `price_type == free`) get a
   Sonnet second pass. Prompt/schema pairs in `automation/prompts/`
   (`enrich_*`, `verify_*`). A manual override that already carries `category` +
   `blurb_en` bypasses the LLM entirely.

5. **`publish.py`.** `build_payload` slims each activity to `_PUBLISHED_FIELDS`
   and adds `categories`/`feature_tags` count arrays for the filter chips.
   `write_latest` writes `data/latest.json`, mirrors to
   `frontend/public/data/latest.json` (gitignored — CI regenerates it), and
   snapshots `data/archive/<run_id>.json`. `commit_and_push` commits latest +
   archive + both caches + `manual_overrides.json` and pushes to `main`.

### Controlled vocabularies

`CATEGORY_VOCAB` and `FEATURE_TAG_VOCAB` in `config.py` are duplicated in three
places that must stay in sync: `config.py`, `automation/prompts/enrich_schema.json`
(+ `verify_schema.json`, currently a copy), and `frontend/src/types.ts` +
`frontend/src/lib/labels.ts`. Changing a vocab means editing all of them.

## Frontend architecture (`frontend/`)

Vite + React + TS + Tailwind + `vite-plugin-pwa`. `vite.config.ts` `base` comes
from `VITE_BASE` (the deploy workflow sets `/what2do_weekwnd/`; dev uses `/`).

- `lib/data.ts` fetches `${BASE_URL}data/latest.json`.
- `lib/filters.ts` — `FilterState`, `DEFAULT_FILTERS`, `applyFilters` (pure
  predicates), and `filtersToParams`/`paramsToFilters` for URL-synced filter
  state. **This is where all filtering logic lives.**
- `lib/format.ts` — date/price/distance display helpers.
- `App.tsx` holds filter state, syncs it to `history.replaceState`, keeps a
  `localStorage` saved-set (`weekwnd.saved.v1`), and splits activities into the
  "weekend" tab (`date_kind != permanent`) vs "places" tab (`date_kind == permanent`).
- `components/ActivityCard.tsx` — Dutch title/description verbatim + a "Translate"
  link to Google Translate (`lib/data.ts#googleTranslateUrl`).

Placeholder PNG icons in `frontend/public/icons/` are solid tangerine squares —
replace with real artwork.

## Deploy (`.github/workflows/deploy-pages.yml`)

Push to `main` touching `frontend/**` or `data/latest.json` → `npm ci` +
`npm run build` in `frontend/` (with `data/latest.json` copied into
`public/data/`) → `actions/deploy-pages`. The weekly pipeline's commit is what
re-triggers this with fresh data.

## Known gaps / next steps

- Coverage is Leuven-city only (`uitinleuven.be`). For wider Vlaams-Brabant /
  Belgium, either add more agenda listing URLs to `UIT_AGENDA_SOURCES` (need a
  server-rendered UiT front-end that paginates via `?page=`) or enable the
  UiTdatabank Search API (`uitdatabank_client.py`, needs a publiq key) which does
  server-side region/coordinate filtering.
- The Claude CLI (`claude -p --json-schema`) exited non-zero in one test env and
  fell back to the Copilot API; if that recurs on the target Mac, raise
  `ENRICH_MAX_BUDGET_USD` / `VERIFY_MAX_BUDGET_USD` or shrink `ENRICH_BATCH_SIZE`.
- `automation/prompts/verify_schema.json` is a byte copy of `enrich_schema.json`;
  narrow it to the correctable fields if desired.
- Placeholder icons in `frontend/public/icons/` are solid squares — replace.
