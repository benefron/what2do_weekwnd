# Brussels support — status and what to run locally

PR: https://github.com/benefron/what2do_weekwnd/pull/1
Branch: `claude/brussels-activities-filters-sources-2uhcmt`

This session runs in a cloud container whose egress proxy blocks every `.be`
host, so anything that needs to actually talk to a Belgian website has to be
run on your Mac. This file is the handoff: what's done, what's still open,
and the exact commands to run.

## What's done (5 commits, pushed, PR open)

1. **Frontend filters** — location picker (presets + "use my location"),
   multi-select age buckets (0-3, 4…10, 11+), a "languages we speak" filter,
   and a "This Wednesday" chip. Distance is recomputed client-side from
   whichever origin you pick, so it works with zero pipeline changes.
2. **Pipeline: Wednesday bucket** — `normalize.py` now buckets the coming
   Wednesday afternoon (school half-day), verified offline against the
   current feed (38 events, 36 of them weekly classes on a sample date).
3. **Pipeline: language rework** — `primary_language` was hard-coded to
   Dutch for every place (including the 130 in Brussels/Wallonia) and the
   enrichment prompt told Claude the readers were one Dutch-speaking family.
   Both fixed; a new `language_free` field flags playgrounds/pools/etc. that
   don't depend on any spoken language. Includes a mandatory enrichment
   cache-version bump (`SCHEMA_VERSION = 2`) — without it the reworded
   prompt would have no effect on the ~647 cached records.
4. **New sources** — two config-only UiT region feeds (Brussels-Capital,
   Vlaams-Brabant), a generic OpenDataSoft fetcher (covers Wallonia/Brabant
   Wallon, no auth needed), a generic RSS/Atom hook, French kid-keywords so
   francophone events survive the prefilter, and `probe_source.py` for
   checking a candidate source without guessing.
5. **Review fixes** — Copilot and Sourcery's automated PR reviews found 4
   real issues (an age bucket that wasn't actually open-ended, a UUID regex
   that dropped legacy IDs, a localStorage bug where opening a shared link
   would silently overwrite your saved prefs, and a chip-order bug that
   could make the same selection serialize two different ways). All four
   fixed, verified, and pushed; review threads resolved.

Full design rationale is in `/root/.claude/plans/i-shared-this-with-delegated-pascal.md`
if you want the "why", though that path is inside this container, not your
Mac — ask me to print it if you want a copy.

## What's NOT done / needs your machine

### 1. Verify the new sources actually work
None of this was fetch-verified — it's built from search-index research, not
a live check. Run this first, before trusting any of it:

```bash
cd ~/path/to/what2do_weekwnd   # wherever you clone/pull the branch
git fetch origin && git checkout claude/brussels-activities-filters-sources-2uhcmt
git pull

cd automation
python3 -m venv .venv        # if you don't already have one
.venv/bin/pip install -r requirements.txt

# the main check — prints OK/EMPTY/FAIL for every configured source
../scripts/verify_sources.sh

# if verify_sources.sh flags a problem, dig in with the probe tool:
.venv/bin/python -m automation.probe_source uit
.venv/bin/python -m automation.probe_source ods odwb_wallonie
.venv/bin/python -m automation.probe_source ods odwb_letsgocity
.venv/bin/python -m automation.probe_source url https://www.quefaire.be/region-de-bruxelles
```

Specific things likely to need a tweak once you can actually see the responses:
- **UiT region pages**: does `?page=N` paginate on
  `uitinvlaanderen.be/agenda/alle/brussels-hoofdstedelijk-gewest/voor-kinderen`,
  or only on the bare `/agenda`? If it doesn't, switch
  `automation/config.py`'s `uitinbrussel`/`uitinvlaamsbrabant` entries to the
  query form (`...?voor-kinderen=1&page=N`) — `first_page` is already there
  as the knob for a JS-hydrated page 0.
- **ODS field names**: `sources._ODS_TITLE` / `_ODS_START` / etc. in
  `automation/sources.py` are candidate-name tuples guessed from the ODWB
  catalogue pages, not confirmed against a real response. `probe_source.py
  ods <key>` prints the actual field names and flags anything unmapped —
  add the real names to those tuples if it complains.
- **opendata.brussels' `agenda` dataset is disabled** (`config.py`,
  `ODS_SOURCES["opendata_brussels"]["enabled"] = False`) until you confirm
  it has a forward-looking horizon rather than literally "today only" — a
  quick `probe_source.py ods opendata_brussels` (after flipping `enabled`
  briefly, or just checking `total_count` / the sample date in its output)
  settles it.

### 2. Run the pipeline for real
```bash
# fast, no Claude spend, just to confirm nothing crashes and the new
# sources/buckets show up
scripts/run_now.sh --no-push --no-enrich

# full run with enrichment — this is what actually re-classifies everything
# under the new prompt and schema version. Expect a one-time spike (~126
# events / ~9 Haiku batches) since SCHEMA_VERSION bumped and invalidated the
# whole enrichment cache; steady-state after that is back to ~1-3 calls.
scripts/run_now.sh --no-push
```

Then sanity-check the two headline claims with:
```bash
python3 -c "
import json, collections
a = json.load(open('data/latest.json'))['activities']
print('primary_language:', collections.Counter(x['primary_language'] for x in a))
print('language_free:', collections.Counter(x.get('language_free') for x in a))
print('wednesday bucket:', sum(1 for x in a if 'wednesday' in x['weekend_bucket']))
"
```
Before this change: `{nl: 469, multi: 5}`, zero `wednesday`. After: Brussels
and Wallonia content should show real `fr`/`multi` values, and `wednesday`
should be non-zero.

### 3. Push the real data once you're happy
```bash
scripts/run_now.sh    # full run + commit + push -> triggers the Pages deploy
```
Or, if you ran with `--no-push` above and just want to commit what you
already generated:
```bash
git add data/ automation/cache/ automation/state/
git commit -m "weekly data refresh"
git push
```

### 4. Merge the PR
Once the sources check out and a real run looks right, merge
https://github.com/benefron/what2do_weekwnd/pull/1 (or ask me to). Both
automated reviewers (Copilot, Sourcery) have had their findings fixed and
their threads resolved as of commit `4769ceb`.

## Known gaps left for later (recorded in CLAUDE.md too)

- `SCHOOL_HOLIDAYS` is Flemish-only — the Fédération Wallonie-Bruxelles
  calendar differs, so `in_school_holiday` is wrong for francophone Brussels
  families. Needs a second table keyed by community.
- Francophone-only Brussels organizers (as opposed to the bilingual big
  venues and what `agenda.brussels` forwards to UiT) are still thin. The
  `api.brussels` agenda API is the canonical fix — trilingual, but
  OAuth-gated and its free tier is unconfirmed.
- `data/places.json` itself wasn't rebuilt for Brussels/Wallonia coverage —
  by design, this round only fixed the *language labels* on existing places.
  `scripts/build_places.sh` is the tool if you want to expand the guide
  itself (~$10 of Claude web search per run, hits a quota after ~7 kinds).
