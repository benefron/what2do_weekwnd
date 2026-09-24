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
