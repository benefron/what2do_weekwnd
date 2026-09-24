#!/bin/bash
# living-ledger — derive ledger entries from commit trailers.
#
# Reads `git log <SYNC_FROM>..HEAD` and turns the trailers in each commit's TRAILING block
# (see _ledger_parse.trailer_lines — the same reader the commit gate uses) into changes:
#
#   Decision: Finding: Opens: Fixed: Action: Retires:  -> a new entry (content-hash id)
#   Closes: F-x          -> F-x becomes CLOSED, with a `✓ closed by <sha>` line
#   Supersedes: D-x      -> D-x becomes SUPERSEDED, with `⤳ superseded by <new id>`
#   Refs: F-x, D-y       -> a `↔ <sha> <subject>` backlink on each
#   Due: Owner: Area: Pin: Date:                 -> modifiers of the entry trailer above them
#   Rejected: Constraint: Directive: …           -> Lore trailers, recorded into that entry
#
# Stateless and deterministic: there is no machine-local bookmark. SYNC_FROM in the committed
# .claude/ledger.conf is a fixed floor (set at install), and everything after it is re-derived
# on every run and deduplicated by id — so every clone on every machine derives the same entries
# from the same history, and a second run changes nothing. Each change is applied once: a closing
# commit leaves its sha on the entry, so a hand re-open is never undone by a re-scan.
#
# Decision:/Retires: rows are also appended to the level-2 log in DECISIONS.md.
#
# ledger-template-version: 4
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/_ledger_lib.sh"

REPO="$(ll_repo_root)"
cd "$REPO" 2>/dev/null || exit 0
git rev-parse --verify -q HEAD >/dev/null 2>&1 || exit 0

LEDGER="$(ll_find_ledger "$REPO")"
[ -n "$LEDGER" ] && [ -f "$LEDGER" ] || exit 0
LEDGER_REL="${LEDGER#$REPO/}"
# LL_LEDGER_FILE / LL_DECISIONS_FILE: sync into copies instead (the session-start digest does
# this, so reading the ledger never dirties the working tree).
[ -n "${LL_LEDGER_FILE:-}" ] && LEDGER="$LL_LEDGER_FILE"

FLOOR="${LL_SYNC_FROM:-$(ll_sync_floor "$REPO")}"   # LL_SYNC_FROM: preview a recovery (with LL_LEDGER_FILE)
MAX="${LL_SYNC_MAX_COMMITS:-5000}"
if [ -n "$FLOOR" ] && git cat-file -e "${FLOOR}^{commit}" 2>/dev/null; then
  RANGE="${FLOOR}..${LL_SYNC_HEAD:-HEAD}"
else
  RANGE="${LL_SYNC_HEAD:-HEAD}"   # no usable floor: bounded scan, dedup keeps it idempotent
fi

DECISIONS_REL="$(ll_conf_get "$REPO" DECISIONS_PATH)"
DECISIONS=""
[ -n "$DECISIONS_REL" ] && [ -f "$REPO/$DECISIONS_REL" ] && DECISIONS="$REPO/$DECISIONS_REL"
[ -n "${LL_DECISIONS_FILE+x}" ] && DECISIONS="${LL_DECISIONS_FILE}"

PYTHONDONTWRITEBYTECODE=1 python3 - "$LEDGER" "$RANGE" "$MAX" "$HERE" "$DECISIONS" \
  "$LEDGER_REL" "$DECISIONS_REL" "$REPO" <<'PY'
import io, re, subprocess, sys
sys.path.insert(0, sys.argv[4])
from _ledger_parse import (ID_RE, ENTRIES_MARKER, KINDS, RELATE_KEYS, MODIFIER_KEYS, LORE_KEYS,
                           hash_id, norm_text, is_legacy, trailer_lines, parse_blocks,
                           split_ledger, entry_text, entry_commit, supersede_ids, insert_by_date,
                           external_prefixes)

LEDGER, RANGE, MAX, _, DECISIONS, LEDGER_REL, DECISIONS_REL, REPO = sys.argv[1:9]
EXT = external_prefixes(REPO)
orig = io.open(LEDGER, encoding='utf-8').read()
if ENTRIES_MARKER not in orig:
    sys.stderr.write(f'living-ledger: {LEDGER_REL} has no {ENTRIES_MARKER} marker — not syncing.\n')
    sys.exit(0)


def git(*a):
    return subprocess.run(['git', *a], capture_output=True, text=True).stdout


SEP = '\x1e'
raw = git('log', '--reverse', f'-n{MAX}', f'--format=%h%x1f%ad%x1f%B{SEP}', '--date=short', RANGE)

existing = [e for e in parse_blocks(split_ledger(orig)[1]) if e['id']]
ids = {e['id'] for e in existing}
# Entries written before content hashing (sequential ids, v1-v3) are recognised by the text they
# were made from, so a re-scan never adds them a second time under a hash id.
legacy_text = {norm_text(entry_text(e)) for e in existing if is_legacy(e['id'])}

IGNORE_FILES = {LEDGER_REL, DECISIONS_REL}
OPENS_ID = re.compile(r'^(F-(?:[0-9a-f]{7}|\d{3,6}))\s+(\S.*)$')


def area_of(sha):
    """The directory (at most two levels deep) that every file of the commit shares, else '-'."""
    files = [f for f in git('show', '--format=', '--name-only', sha).splitlines()
             if f.strip() and f not in IGNORE_FILES and not f.startswith(('.claude/', '.githooks/'))]
    if not files or any('/' not in f for f in files):
        return '-'
    common = files[0].split('/')[:-1]
    for f in files[1:]:
        parts = f.split('/')[:-1]
        n = 0
        while n < min(len(common), len(parts)) and common[n] == parts[n]:
            n += 1
        common = common[:n]
    return '/'.join(common[:2]) or '-'


commits = []      # [(sha, date, subject, [entry dicts], relate actions)]
for rec in raw.split(SEP):
    rec = rec.strip('\n')
    if not rec:
        continue
    parts = rec.split('\x1f', 2)
    if len(parts) < 3:
        continue
    sha, date, body = parts[0].strip(), parts[1].strip(), parts[2]
    subject = next((l.strip() for l in body.strip().splitlines() if l.strip()), '')
    tl = trailer_lines(body)
    if not tl:
        continue
    made, actions, commit_wide = [], [], []
    for line in tl:
        key, _, text = line.partition(':')
        text = text.strip()
        if key in RELATE_KEYS:
            olds, news = supersede_ids(text) if key == 'Supersedes' else (ID_RE.findall(text), [])
            ours = [i for i in olds if i.split('-')[0] not in EXT]
            theirs = [i for i in ID_RE.findall(text) if i.split('-')[0] in EXT]
            actions += [(key, i, news) for i in ours]
            if theirs:                                # ids of the other register (EXTERNAL_IDS):
                commit_wide.append(('Refs', ', '.join(theirs)))   # kept as a pointer, not applied
        elif key in KINDS and text:
            made.append(dict(key=key, text=text, extra=[]))
        elif key in MODIFIER_KEYS or key in LORE_KEYS:
            # binds to the entry trailer directly above it; before any, to all of them
            (made[-1]['extra'] if made else commit_wide).append((key, text))
    commits.append((sha, date, subject, made, actions, commit_wide))

existing_ids = set(ids)
new_blocks, closes, log_rows = [], [], []
for sha, date, subject, made, actions, commit_wide in commits:
    area_auto = None
    decision_ids = []
    blocks = []
    for m in made:
        key, text = m['key'], m['text']
        prefix, typ, status = KINDS[key]
        mods = commit_wide + m['extra']
        get = lambda k: next((v for kk, v in reversed(mods) if kk == k), '')
        eid = None
        om = OPENS_ID.match(text) if key == 'Opens' else None
        if om:                              # `Opens: F-x <text>` — an id chosen up front
            eid, text = om.group(1), om.group(2)
            if eid in existing_ids and not any(e['id'] == eid and entry_commit(e) == sha
                                               for e in existing):
                eid = None                  # taken by another entry: never drop, re-key
        if eid is None:
            eid = hash_id(prefix, text)
        if typ == 'decision':
            decision_ids.append(eid)
        if eid in ids or norm_text(text) in legacy_text:
            continue
        ids.add(eid)
        area = re.sub(r'\s+', '-', get('Area')) if get('Area') else None
        if area is None:
            area_auto = area_auto or area_of(sha)
            area = area_auto
        hdate = date
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', get('Date')) and get('Date') != date:
            hdate = f"{get('Date')} (recorded {date})"
        lines = [text]
        if get('Due') and status == 'OPEN':
            lines.append(f"· Due: {get('Due')}")
        if get('Owner'):
            lines.append(f"· Owner: {get('Owner')}")
        if get('Pin').lower() in ('yes', 'true', '1', 'y'):
            lines.append('· Pinned')
        lines += [f'· {k}: {v}' for k, v in m['extra'] if k in LORE_KEYS]
        lines += [f'· {k}: {v}' for k, v in commit_wide if k in LORE_KEYS or k == 'Refs'
                  ]
        blocks.append(f'## {eid} · {status} · {typ} · {area} · {hdate}\n'
                      + '\n'.join(lines) + f'\n→ commit {sha}\n')
        if typ in ('decision', 'retired'):
            log_rows.append((date, eid, text, sha))
    new_blocks += list(reversed(blocks))    # oldest first; inserted by date, trailer order kept
    for key, tid, by in actions:
        closes.append((key, tid, sha, subject, ', '.join(by or decision_ids)))
s = orig
for b in new_blocks:
    s = insert_by_date(s, b)


def find_block(text, eid):
    m = re.search(rf'^## {re.escape(eid)} · .*$', text, re.M)
    if not m:
        return None
    start = m.start()
    nxt = re.search(r'^## ', text[m.end():], re.M)
    end = m.end() + (nxt.start() if nxt else len(text) - m.end())
    return start, end


def edit_block(text, eid, status_from, status_to, line, applied):
    """Append `line` to entry eid and flip its status — once. `applied(l)` recognises a body
    line left by an earlier run, in which case nothing happens (so a hand re-open is never
    undone by a re-scan). -> (text, found)."""
    span = find_block(text, eid)
    if not span:
        return text, False
    start, end = span
    block = text[start:end]
    if any(applied(l.strip()) for l in block.splitlines()[1:]):
        return text, True
    hdr, _, rest = block.partition('\n')
    if status_to and re.match(rf'^## {re.escape(eid)} · ({"|".join(status_from)}) ', hdr):
        hdr = re.sub(r'^(## \S+ · )\S+', lambda mm: mm.group(1) + status_to, hdr)
    trail = block[len(block.rstrip('\n')):]
    rest = rest.rstrip('\n')
    new = hdr + ('\n' + rest if rest else '') + '\n' + line + (trail if trail else '\n')
    return text[:start] + new + text[end:], True


seen = set()
for kind, tid, sha, subject, extra in closes:
    if (kind, tid, sha) in seen:
        continue
    seen.add((kind, tid, sha))
    subj = subject[:90]
    if kind == 'Refs':
        s, found = edit_block(s, tid, (), '', f'↔ {sha} {subj}'.rstrip(),
                              lambda l, h=sha: l.startswith(f'↔ {h}'))
    elif kind == 'Closes':
        s, found = edit_block(s, tid, ('OPEN',), 'CLOSED', f'✓ closed by {sha} {subj}'.rstrip(),
                              lambda l, h=sha: l.startswith(f'✓ closed by {h}'))
    else:
        what = f'superseded by {extra}' if extra else 'superseded'
        s, found = edit_block(s, tid, ('OPEN', 'CLOSED', 'STANDING'), 'SUPERSEDED',
                              f'⤳ {what} in {sha} {subj}'.rstrip(),
                              lambda l, h=sha: l.startswith('⤳ ') and f' in {h}' in l)
    if not found:
        sys.stderr.write(f'living-ledger: {kind} {tid} in {sha} names no ledger entry — ignored.\n')

# Strip the in-ledger sync markers of template v1.
s = re.sub(r'^[ \t]*<!-- (LEDGER_SYNC|last_synced_commit)[^\n]*-->[ \t]*\n?', '', s, flags=re.M)
s = re.sub(r'\n{3,}', '\n\n', s)
if s != orig:
    io.open(LEDGER, 'w', encoding='utf-8').write(s)

# --- level 2: append the dated fact to the decisions log --------------------------
if log_rows and DECISIONS:
    START, END = '<!-- DECISIONS_LOG_START -->', '<!-- DECISIONS_LOG_END -->'
    try:
        d = io.open(DECISIONS, encoding='utf-8').read()
    except OSError:
        d = ''
    if START in d and END in d:
        head, _, rest = d.partition(START)
        rows, _, tail = rest.partition(END)
        added = []
        # follow the log's existing row style: a markdown table (default) or `date · id · …` lines
        dotted = bool(re.search(r'^\d{4}-\d{2}-\d{2} · ', rows, re.M)) and '| ' not in rows
        for date, eid, text, sha in log_rows:
            if re.search(rf'(^|[|·] ?){re.escape(eid)}( ?[|·]|$)', rows, re.M):
                continue
            if dotted:
                added.append(f'{date} · {eid} · {text.strip()} · {sha}')
            else:
                added.append(f'| {date} | {eid} | {text.replace("|", chr(92) + "|").strip()} | `{sha}` |')
        if added:
            rows = '\n' + rows.strip('\n') + ('\n' if rows.strip('\n') else '') + '\n'.join(added) + '\n'
            io.open(DECISIONS, 'w', encoding='utf-8').write(head + START + rows + END + tail)
PY
exit 0
