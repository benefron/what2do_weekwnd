# Project Ledger — decisions, findings, retired framings

**This is the single register.** If you want to know what was decided, what is open, or what has
already been settled and must not be re-litigated, it is here. Newest first.

**Read this before proposing anything that sounds new.** A large fraction of the entries below are
questions that were already asked and answered — sometimes answered wrongly first, then corrected.
Re-opening one costs more than reading it.

This file is an instance of the **Lore** pattern (git commit messages as a structured knowledge
protocol for AI coding agents — arXiv:2603.15566). The commit trailer is the atomic unit of
institutional knowledge; this file is the pre-digested read surface over those trailers.

---

## How to use this file

**Finding things.** Grep it. Every entry is one greppable block; the terms you would naturally
search for appear in the entry bodies deliberately.

```bash
grep -A4 '^## F-'       LEDGER.md   # all findings
grep -A4 '· OPEN ·'     LEDGER.md   # everything still open
grep -A4 '· retired ·'  LEDGER.md   # do not re-propose these
grep -B1 -A4 '<topic>'  LEDGER.md   # everything about one topic
```

**Three levels of a decision.** A decision is captured at up to three levels, each for a different
reader, and the levels must agree:

| Level | Where | Reader | What it holds |
|---|---|---|---|
| 1. Fact | **this file**, via the `Decision:` trailer of the commit that enacted it | future sessions, the injected digest | one sentence, status, evidence pointer |
| 2. Reasoning | the **body of the commit** that carried the trailer, and **`DECISIONS.md`** when it needs more room — append-only, every section dated | you, collaborators | what was decided (quoting level 1), why — the numbers and the `F-`/`D-` ids, what was rejected and why, where it lives, what validation is still pending |
| 3. Audience | *only if this repo has an audience surface* — a deck script, a paper, the README's claims. `.claude/ledger.conf` names it under `AUDIENCE_SURFACE=` | that audience | the current state. Updated **on request**; nothing rebuilds it automatically, and it never rewrites history |

The order is fixed: the trailer goes on the commit → the post-commit hook writes level 1 → `ledger-sync.sh`
appends the dated fact to level 2's log → a human writes the reasoning above it. Level 3 moves only when
you ask for it.

A decision that exists at level 1 only is a verdict with no argument behind it. A decision that exists at
level 2 only is invisible to every future session. Both are incomplete.

**Level 2 is append-only and keeps its history.** A superseded decision is never deleted and never
edited: it gains a `**Superseded by:** <section / ledger id> · <date>` line, and the section that
replaces it opens with `**Supersedes:** …`. The corrections *are* the record.

**Entry format.** The header line is machine-parsed — keep it exact.

```
## <ID> · <STATUS> · <type> · <area|-> · <date>
<one or two lines of what and why>
→ <file:line evidence> · <pointer or closes-with>
```

| Field | Values |
|---|---|
| `ID` | `D-` decision · `F-` finding · `A-` action · `R-` retired framing · `N-` note/thought, then 7 hex: a content hash of the entry text (`F-3fa9c1e`), so the same entry gets the same id on every clone and branch — never a counter. Older sequential ids (`F-014`) stay valid and are never renumbered. |
| `STATUS` | `OPEN` (something to resolve) · `CLOSED` (resolved; for a decision: settled and in force) · `SUPERSEDED` (replaced — see its `⤳` line) · `STANDING` (a settled fact, a retired framing, a note) |
| `type` | `decision` · `finding` · `action` · `retired` · `note` · `thought` |
| `area` | a short workstream / component tag, or `-` if it belongs to none |
| `date` | `YYYY-MM-DD`, optionally suffixed `(recorded)`, `(from <sha>)` or `(backfilled)` |

**Date honesty.** A date is only ever one of:
- **bare** — the entry was written on that date, live.
- **`(recorded)`** — transcribed from a doc that already carried that date.
- **`(from <sha>)`** — recovered via `git log -S`, i.e. the commit that introduced the claim.
- **`(backfilled)`** — reconstructed with no better evidence; treat the date as approximate.

Never write an undecorated date onto a backfilled entry. Inventing a tidy history is the exact
failure this file exists to prevent.

**Nothing is ever deleted.** Status changes; git holds the history.

---

## How entries get here

Three capture paths, all cheap:

1. **Commit trailers.** Every commit carries one (the `commit-msg` gate checks);
   `.claude/hooks/ledger-sync.sh` reads `git log` and records any it has not seen. Vocabulary:
   ```
   Decision:   <one line>              a settled choice               -> D-…, CLOSED (in force)
   Finding:    <one line>              a settled fact / result        -> F-…, STANDING
   Opens:      <one line>              an open problem or question    -> F-…, OPEN
   Fixed:      <one line>              found and resolved right here  -> F-…, CLOSED
   Action:     <one line>              a to-do                        -> A-…, OPEN
   Closes:     F-3fa9c1e               resolves an open entry
   Retires:    <the framing>           an approach that is now dead   -> R-…, STANDING
   Supersedes: D-8b1e0d2               with a Decision: — marks the old one SUPERSEDED
   Refs:       F-3fa9c1e, D-004        relates this commit to existing entries (backlink)
   Due:        2026-10-15              modifier: a date the open item must be resolved by
   Owner:      sam                     modifier: who holds it
   Area:       hiring                  modifier: the workstream (default: the directory touched)
   Pin:        yes                     modifier: keep it in the session digest while in force
   Date:       2026-09-14              modifier: decided earlier -> `(recorded <commit date>)`
   ```
   Modifiers and Lore trailers (`Rejected:`, `Directive:`, `Constraint:`) belong to the entry
   trailer directly above them and are recorded into its body.
   **A decision that changes no file** — a direction, a priority, a call made in a meeting — is an
   empty commit: `git commit --allow-empty --only -m "decide: <subject>" -m "Decision: <one line>"`.
   It is dated, attributed, and in the log like any other.
   `.githooks/commit-msg` **rejects** a commit whose last paragraph carries none of these. The
   explicit opt-out is `Ledger: none — <reason, 3+ words>`; the escape hatch is
   `git commit --no-verify`. Merges, reverts, `fixup!`/`squash!`, `[bot]` authors and
   `EXEMPT_SUBJECTS` are exempt. `.githooks/post-commit` then syncs and commits this file itself.
2. **Checkpoint proposals.** At the end of a work chunk Claude says *"recording these: …"*; you
   approve, edit or decline.
3. **"note that …"** in chat → appended immediately as a `note` or `thought`. A thought is typed as
   a thought so it is visibly not a decision.

A digest of `OPEN` items and recent decisions is injected automatically at session start and after
compaction by `.claude/hooks/digest.sh` — so a cold session already knows this, without searching.

**Machines and branches.** Trailer-born entries are re-derived from git history on every clone
(from `SYNC_FROM` in `.claude/ledger.conf`) — there is no per-machine bookmark, so every clone
derives the same entries. Two branches or two machines inserting entries at once are merged
entry-wise by `.claude/hooks/ledger-merge.py` (`.gitattributes: merge=ledger`): no conflict,
`CLOSED` beats `OPEN`, and nothing is dropped. **Never delete an entry** — the next sync would
restore a trailer-born one. Mark it `SUPERSEDED` instead.

---

# Entries

<!-- newest first; ledger-sync.sh inserts directly below this marker -->
<!-- ENTRIES_START -->

## D-380e8c5 · CLOSED · decision · - · 2026-09-24
this repo keeps a living ledger at LEDGER.md; every commit carries a ledger trailer
→ commit 7a133f0

## D-0962e61 · CLOSED · decision · frontend-deps · 2026-09-22 (from e50ee01)
TypeScript is held at 6.x until typescript-eslint supports 7; a Dependabot TS-7 PR failing `npm run lint` is the intended upgrade signal
✗ rejected: a postinstall script symlinking a second TypeScript copy into nine packages plus `legacy-peer-deps=true` — too fragile for a linter, and the flag silences every future peer conflict
→ frontend/eslint.config.js header, frontend/package.json
→ commit e50ee01

## D-83feaf7 · CLOSED · decision · watchdog · 2026-09-21 (from f5b5aef)
the watchdog counts retries per failure episode, not per run_id: every retry spawns a fresh run_id, so a per-run_id counter reset each time and WATCHDOG_MAX_RETRIES was unreachable
→ automation/watchdog.py `_episode_over`, WATCHDOG_EPISODE_RESET_SECONDS (24h)
→ commit f5b5aef

## D-984f4f8 · CLOSED · decision · enrich · 2026-09-22 (from 7895049)
the LLM contract is age- and language-neutral; fits_4yo / fits_8yo / french_required are retired and SCHEMA_VERSION is 4, so every cached event was re-enriched once
→ automation/prompts/*, automation/enrich.py; old `age=4yo|8yo|both` URLs still map to buckets in frontend/src/lib/filters.ts
→ commit 7895049

## D-9fbb505 · CLOSED · decision · publish · 2026-09-22 (from 1912a66)
publish.commit_and_push fetches and rebases the data commit onto origin/main before pushing; a rebase conflict aborts and fails loud, never force-pushes
→ automation/publish.py `_sync_with_remote`; two PRs merged during the 2026-09-22 run had left local main 7 commits behind
→ commit 1912a66

## D-a010ed0 · CLOSED · decision · enrich · 2026-09-21 (from dfe7e63)
a record whose required LLM field was defaulted (key missing from the model answer) is never written to the enrichment cache; it keeps its defaults for the run and is re-asked next run
→ automation/enrich.py `_has_defaulted_field`, `_REQUIRED_LLM_FIELDS`; same class of bug as the geocode cache's null-forever entries
→ commit dfe7e63

## D-2d36d33 · CLOSED · decision · frontend · 2026-09-22 (from 48fb4b1)
the Google Maps link searches by venue name + address, not coordinates, so Maps opens the venue's own listing; lat,lng is only the fallback and no link is shown when nothing is locatable
→ frontend/src/lib/data.ts `googleMapsUrl`
→ commit 48fb4b1

## F-b5b8edf · STANDING · finding · calendars · 2026-09-22 (from 0b6e161)
SCHOOL_HOLIDAYS_NL had herfstvakantie 2026 (real: 2–8 Nov) and krokusvakantie 2027 (real: 8–14 Feb) each a week off; corrected against a Leuven school's published calendar. The 2027–29 entries come from secondary sources (kampkompas.be, gezondheid.be), not onderwijs.vlaanderen.be
→ automation/config.py, automation/tests/test_config_calendars.py
→ commit 0b6e161

## F-3e92e14 · STANDING · finding · normalize · 2026-09-22 (from af01a02)
normalize treated any activity with no occurrences[] as still in the future, so a multi-day span that ended weeks ago survived into the feed and got holiday flags; found by the shared bucket-parity fixture
→ automation/normalize.py `_is_future_or_ongoing`, automation/tests/fixtures/bucket_cases.json
→ commit af01a02

## R-793cec5 · STANDING · retired · watchdog · 2026-09-21 (from 872ebe7)
detecting a dead weekly run from the stale lock alone: run_weekly unlinks the lock in `finally` on every exit, so a clean abort (2026-09-21, DNS down after wake, 0 records) left no lock and the watchdog was blind
→ superseded by the no-lock path in automation/watchdog.py and `stage: "failed"` in run_progress.json
→ commit 872ebe7
