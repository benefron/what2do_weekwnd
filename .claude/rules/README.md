# Path-scoped rules

Files in `.claude/rules/*.md` are delivered to Claude **at the moment a matching file is
touched** — the point where a stale assumption would actually cause damage. Use them to
pin down known-open findings and hard "do not do this" constraints for a specific area of
the code, so they never have to be re-derived.

This is the local, point-of-contact companion to `LEDGER.md` (the whole-project register)
and the session-start digest (the bounded overview). A rule is worth writing when an open
`F-` entry keeps getting re-discovered, or when a value/pattern is dangerous to touch
without context.

## Format

Front-matter lists the globs; the body is what Claude sees on contact.

```markdown
---
paths:
  - "src/config.py"
  - "src/**/settings*.py"
---

# Before touching config in this area — read this

- **F-3fa9c1e · <the finding>.** <one or two lines. Point at `file:line`.> Do not re-derive it.
- **Settled — do not re-propose:** <the thing that keeps coming back, and why it's closed>.
```

## Guidance

- Keep each rule to the findings that matter for *that* path. A rule that fires on every
  file is noise.
- Reference the `F-`/`D-`/`R-` id so the reader can grep the full entry in `LEDGER.md`.
- When a finding is closed or a decision superseded, update or delete its rule in the same
  commit. The session-start digest and the cross-repo dashboard report a rule that still cites a
  closed finding/action or a superseded entry as a **stale rule**, so this does not stay quiet.
  (A rule citing a decision that is still in force is fine — decisions are born `CLOSED`.)
- Lore's `Directive:` trailer is the commit-time equivalent — a forward-looking
  instruction to future modifiers. A recurring `Directive:` about one area belongs here
  as a rule.

<!-- ledger-template-version: 4 -->
