# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A "what to do with the kids (4 & 8) in Belgium" dashboard for a family in Leuven
(Dutch speakers, no French). A local Python pipeline scrapes/enriches events and
writes JSON into the repo; GitHub Actions only builds and deploys the frontend.
Modelled on `../israel-news-digest`.

**Two data tiers:**
- `data/latest.json` — the **weekly events feed**: dated events, refreshed every
  Monday by `run_weekly.py`. The enrichment cache means only new/changed events
  cost Claude tokens.
- `data/places.json` — the **permanent guide**: museums, zoos, provincial
  domains, speelbossen, playgrounds, attraction parks, zomerbars,
  playground-restaurants across all of Belgium. **Not re-fetched weekly** —
  `run_weekly.py` reads it verbatim and merges it in. Expand it with
  `scripts/build_places.sh` (Claude web search) or by hand;
  `source: "curated"` entries are never overwritten by the builder.

The frontend has four tabs: `weekend` (dated events), `places` (permanent, minus
the two below), `zomerbar`, `eatplay` (playground-restaurants) — split by
`Activity.kind`.

## Commands

```bash
# Pipeline (from repo root)
scripts/verify_sources.sh                    # ping every source; run this before trusting a change
scripts/build_places.sh            # rebuild/expand data/places.json (manual, ~$10 of web search)
scripts/build_places.sh --kinds zomerbar,speelbos   # just some kinds
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

`run_weekly.py` orchestrates: fetch → normalize/prefilter/dedupe → geocode →
distance prefilter → enrich → `family_relevant` filter → merge `places.json` →
publish. Guards copied from israel-news-digest's `run_daily.py`: file lock
(`state/run.lock`), `MIN_HOURS_BETWEEN_RUNS` (~20h) idempotency window, and
**abort without overwriting `data/latest.json`** if a stage yields nothing.
`places.load_places_as_activities()` reads `data/places.json` verbatim (no fetch,
no enrich) — `build_places.py` is the only thing that writes it.

1. **`sources.py` — fetch.** Flat list of raw records tagged `_kind` =
   `uitdatabank` | `claude_search` | `manual`.
   - `fetch_uit_agenda()`: for each entry in `config.UIT_AGENDA_SOURCES`
     (`uitinleuven` city + `uitinvlaanderen` all-Flanders), scrape the
     server-rendered `?page=N` listing (per-source `max_pages`/`first_page`) for
     `/agenda/e/<slug>/<uuid>` links, dedupe UUIDs across sources, then hydrate
     each via the **public, no-auth** `https://io.uitdatabank.be/events/<uuid>` —
     full UiTdatabank JSON-LD (`name`/`description` multilingual, `typicalAgeRange`
     as `"6-11"`, `priceInfo[]`, `calendarType`+`startDate`/`subEvent`, `terms`,
     `location.geo`). Not schema.org shape.
   - `claude_search.fetch_events()` (`config.CLAUDE_SEARCH_ENABLED`): a Sonnet
     `claude -p --allowedTools WebSearch WebFetch --json-schema` call (via
     `llm_runner.run_search_with_schema`, no Copilot fallback) that finds the
     big-name touring acts the agendas miss — internationally known concerts,
     musicals, major family shows anywhere in Belgium. Returns records with
     classification already filled → they bypass `enrich.py`.
   - `data/manual_overrides.json`.
   `http_get()` retries a 403 via `tls_client`. A dead source logs and is
   skipped. `config.UITDATABANK_ENABLED` (publiq creds in `secrets.local.json`)
   also pulls `uitdatabank_client.py` (paid Search API).

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

   Between geo and enrich, `run_weekly.py` runs a **distance prefilter** (drop
   agenda events with `distance_km > config.MAX_DISTANCE_KM`; curated sources
   kept). After enrich it runs a **`family_relevant` filter** — Claude marks
   adult-only films / courses / nightlife `family_relevant: false` and they're
   dropped; kept = things to do with the kids + big-name concerts/shows.

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

`CATEGORY_VOCAB` and `FEATURE_TAG_VOCAB` in `config.py` are duplicated in
`automation/prompts/enrich_schema.json` (+ `verify_schema.json`, a byte copy) and
`frontend/src/types.ts` + `frontend/src/lib/labels.ts`. `family_relevant` is a
Claude-set boolean in the same schema — `run_weekly.py` drops `false`. The place
`kind` enum lives in `data/places.json`, `automation/build_places.py` (`KINDS`),
`automation/places.py` (`_KIND_TO_CATEGORY`), `frontend/src/types.ts` (`PlaceKind`)
and `frontend/src/lib/labels.ts`. Changing any vocab means editing every copy.

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

- `build_places.py` / `claude_search` web-search calls hit a Claude server-tool
  quota after ~7 kinds in a session and exit non-zero (no fallback — the kind is
  just skipped). `scripts/build_places.sh` has `--dedupe` (cross-kind, keeps the
  most specific `kind` via `KIND_RANK`) and `--images` (backfill og:image) modes
  that don't call the LLM. Nominatim also burst-limits — re-run to fill the
  `lat: null` stragglers from the shared cache.
  `scripts/_oneshot_buildplaces.sh` + its `com.benefron.weekwnd-oneshot`
  LaunchAgent are a **self-deleting** job that fills the remaining gap kinds
  (zoo/multimove/playground_outdoor) the next day at 10:00 and then uninstalls
  itself; delete the plist to cancel.
- Agenda coverage is `uitinleuven` + `uitinvlaanderen` (all Flanders). Wallonia
  and non-UiT venues rely on the `claude_search` pass. Add more `?page=`-paginated
  UiT front-ends to `UIT_AGENDA_SOURCES`, or enable the UiTdatabank Search API
  (`uitdatabank_client.py`, publiq key) for real server-side region/radius filtering.
- The Claude CLI (`claude -p --json-schema`) exited non-zero in one test env and
  fell back to the Copilot API; if that recurs on the target Mac, raise
  `ENRICH_MAX_BUDGET_USD` / `VERIFY_MAX_BUDGET_USD` or shrink `ENRICH_BATCH_SIZE`.
- `automation/prompts/verify_schema.json` is a byte copy of `enrich_schema.json`;
  narrow it to the correctable fields if desired.
- Placeholder icons in `frontend/public/icons/` are solid squares — replace.
