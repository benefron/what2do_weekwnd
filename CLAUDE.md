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
`Activity.kind`. It is no longer specific to one family: you pick where you are,
which ages you're shopping for, and which languages you speak.

## Commands

```bash
# Pipeline (from repo root)
scripts/verify_sources.sh                    # ping every source; run this before trusting a change
automation/.venv/bin/python -m automation.probe_source uit           # what a listing page actually returns
automation/.venv/bin/python -m automation.probe_source ods odwb_wallonie   # real ODS field names
automation/.venv/bin/python -m automation.probe_source url https://www.quefaire.be/region-de-bruxelles
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
   `uitdatabank` | `ods` | `feed` | `claude_search` | `manual`.
   - `fetch_uit_agenda()`: for each entry in `config.UIT_AGENDA_SOURCES`
     (`uitinbrussel` + `uitinvlaamsbrabant` region feeds, then `uitinleuven`
     city + `uitinvlaanderen` all-Flanders — **order matters**, uuids seen from
     an earlier source are skipped so the narrow feeds keep their own label),
     scrape the
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
   - `fetch_opendatasoft()`: OpenDataSoft v2.1 portals (`config.ODS_SOURCES`) —
     public, no auth, structured dated events. Covers Wallonia/Brabant Wallon,
     which UiTdatabank barely reaches. Dataset field names vary, so
     `sources._ODS_*` are candidate-name tuples rather than one fixed schema.
   - `fetch_feeds()`: RSS/Atom via `feedparser` (`config.FEED_SOURCES`, empty by
     default) — the cheap hook for any feed found later.
   - `data/manual_overrides.json`. Also the right home for anything found by
     hand (e.g. a Facebook group): an entry carrying `category` + `blurb_en`
     bypasses the LLM entirely.
   `http_get(url, lang)` takes a per-source language so a French source gets
   `Accept-Language: fr-BE` instead of the global `nl-BE`.
   `http_get()` retries a 403 via `tls_client`. A dead source logs and is
   skipped. `config.UITDATABANK_ENABLED` (publiq creds in `secrets.local.json`)
   also pulls `uitdatabank_client.py` (paid Search API).

2. **`normalize.py` — raw → partial `Activity`, prefilter, dedupe.**
   `_from_uitdatabank` / `_from_manual` map each `_kind`.
   `id = sha1(canonicalize_url(public_url))[:10]`. Parses `calendarType`/`subEvent`
   into `occurrences[]` + `date_kind` (`single|multi_day|recurring|permanent`);
   computes `weekend_bucket`
   (`wednesday|this_weekend|next_weekend|school_holiday|later`, including
   span-overlap for multi-day runs) and `in_school_holiday` from
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
`frontend/src/types.ts` + `frontend/src/lib/labels.ts`. `family_relevant` and
`language_free` are Claude-set booleans in the same schema — `run_weekly.py`
drops `family_relevant: false`; `language_free` ("enjoyable without following any
spoken language") is what lets the language filter keep playgrounds and pools for
a speaker of any language. Anything added to `enrich._LLM_FIELDS` **must** also
go in `enrich._default_fields` (a cache hit does
`cached.get(f, _default_fields(act)[f])` and would otherwise `KeyError`) and in
`publish._PUBLISHED_FIELDS` (an unlisted field is silently dropped), and needs
`enrich.SCHEMA_VERSION` bumped — it is folded into `_content_hash`, so without a
bump every cached record replays the old field set and prompt changes do nothing. The place
`kind` enum lives in `data/places.json`, `automation/build_places.py` (`KINDS`),
`automation/places.py` (`_KIND_TO_CATEGORY`), `frontend/src/types.ts` (`PlaceKind`)
and `frontend/src/lib/labels.ts`. Changing any vocab means editing every copy.

## Frontend architecture (`frontend/`)

Vite + React + TS + Tailwind + `vite-plugin-pwa`. `vite.config.ts` `base` comes
from `VITE_BASE` (the deploy workflow sets `/what2do_weekwnd/`; dev uses `/`).

- `lib/data.ts` fetches `${BASE_URL}data/latest.json`.
- `lib/filters.ts` — `FilterState`, `DEFAULT_FILTERS`, `applyFilters` (pure
  predicates), and `filtersToParams`/`paramsToFilters` for URL-synced filter
  state. **This is where all filtering logic lives.** Ages are multi-select
  buckets matched by span overlap against `age_min`/`age_max` (null / 99 / ≥18
  all mean "no upper bound"); languages keep anything `multi` or `language_free`
  as well as the selected ones. `paramsToFilters(search, base)` layers URL params
  over saved prefs — **the URL always wins**, so shared links are stable.
- `lib/locations.ts` — `HOME_LOCATIONS` presets, `haversineKm` (mirrors
  `automation/geo.py` exactly), and `withDistance`, which re-derives
  `distance_km` for the chosen origin in `App.tsx` **before** filtering. Because
  of that, the predicate, the sort and the card all still just read
  `activity.distance_km`; nothing else knows about origins. The shipped
  `distance_km` is the Leuven fallback.
- `lib/format.ts` — date/price/distance display helpers.
- `App.tsx` holds filter state, syncs it to `history.replaceState`, keeps a
  `localStorage` saved-set (`weekwnd.saved.v1`) plus remembered filter prefs
  (`weekwnd.prefs.v1` — origin, ages, languages), and splits activities into the
  "weekend" tab (`date_kind != permanent`) vs "places" tab (`date_kind == permanent`).
- `components/ActivityCard.tsx` — title/description verbatim + a "Translate"
  link to Google Translate (`lib/data.ts#googleTranslateUrl`, source language
  taken from `primary_language`).
- `components/FilterBar.tsx` — `Toggle` (single-select) and `MultiToggle`
  (multi-select) chip rows; use them rather than hand-rolling a fourth copy.
  Picking the "This Wednesday" chip also clears `hideClasses`, because the weekly
  classes that flag hides are most of what actually runs on the school half-day
  (38 events vs 2 on a sample Wednesday).

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
- **The Brussels/Wallonia sources are structurally researched but not
  fetch-verified** — they were added from an environment where every `.be` host
  is blocked. Before trusting them run `scripts/verify_sources.sh` and
  `probe_source.py`. Specifically unconfirmed: whether `?page=N` paginates on the
  faceted UiT paths (`first_page` is the knob if page 0 is JS-hydrated); the real
  ODS field names (`sources._ODS_*` are candidate lists, and the probe prints the
  actual ones); and whether opendata.brussels' `agenda` dataset has a forward
  horizon or is literally "du jour" — it is `enabled: False` until that is known.
- `_EVENT_LINK_RE` requires exactly 36 chars, but publiq still emits legacy
  CDBIDs in an 8-4-4-16 uppercase shape (35). If Brussels yield looks
  suspiciously low, loosen it to `[0-9A-Fa-f-]{32,40}`.
- Agenda coverage is UiT (Flanders + the Dutch-speaking Brussels offer) plus the
  ODWB OpenDataSoft datasets for Wallonia. Francophone Brussels is still thin:
  events encoded into `agenda.brussels` are forwarded to UiT the next day, which
  is why it partly works, but francophone-only organisers are missed. The
  canonical fix is the `api.brussels` agenda API (trilingual, OAuth-gated,
  free-with-registration unconfirmed) — same `secrets.local.json` pattern as
  `UITDATABANK_ENABLED`. The UiTdatabank Search API would give real server-side
  radius filtering but the Basic plan is €125/year; the free test environment is
  worth using to measure coverage first.
- **`SCHOOL_HOLIDAYS` is Flemish-only.** The Fédération Wallonie-Bruxelles
  calendar differs, so `in_school_holiday` / `school_holiday_name` are wrong for
  francophone Brussels families. Needs a second table keyed by community.
- The Claude CLI (`claude -p --json-schema`) exited non-zero in one test env and
  fell back to the Copilot API; if that recurs on the target Mac, raise
  `ENRICH_MAX_BUDGET_USD` / `VERIFY_MAX_BUDGET_USD` or shrink `ENRICH_BATCH_SIZE`.
- `automation/prompts/verify_schema.json` is a byte copy of `enrich_schema.json`;
  narrow it to the correctable fields if desired.
- Placeholder icons in `frontend/public/icons/` are solid squares — replace.
