#!/usr/bin/env python3
"""living-ledger — shared ledger parser, id helpers and trailer reader.

Copied verbatim into each repo as .claude/hooks/_ledger_parse.py. Entrypoints:

    _ledger_parse.py digest <LEDGER> <cutoff-date> <max_open> <max_recent> [<recent-paths-file>]
        -> the bounded session-start digest (markdown on stdout, empty if no entries).
           Overdue items first, then open items in the area being worked on.

    _ledger_parse.py block <LEDGER> <repo_id> <repo_path> <head> <state> <since> [<version>] [<max_open>]
        -> this repo's block for the cross-repo dashboard

    _ledger_parse.py stale-rules <LEDGER> <rules_dir>
        -> one line per .claude/rules/*.md that cites a closed finding or a superseded entry

    _ledger_parse.py lint <LEDGER>
        -> one line per defect the parser would otherwise skip silently

    _ledger_parse.py id <P> <text>
        -> the content-hash id for a new entry (e.g. F-3fa9c1e)

    _ledger_parse.py tidy-status <LEDGER> <repo_root> [<max_open>]
        -> one line saying why a tidy is due (volume of work + time since the last `Tidy:`
           commit), or nothing

    _ledger_parse.py tidy-report <LEDGER> <repo_root> [<max_open>]
        -> the candidates a /ledger-tidy pass reviews (markdown)

    _ledger_parse.py share-state <repo_root> [--tsv] [<index_home> <this_host> <repo_id>]
        -> where ledger records exist that this checkout has not shared: unpushed, not pulled
           (as of the last fetch), on other local branches / worktrees, and — from the private
           index — unpushed on other machines. Human lines, or one TSV row (--tsv).

    _ledger_parse.py cli <repo_root> <command> [args]     (what `.claude/hooks/ledger` runs)
        -> the query side of the Lore protocol over this repo, for any agent or person:
           context|directives|constraints|rejected <path>, open, decisions, retired,
           stale, validate, rules, tidy, share, search. `ledger help` lists them.

    _ledger_parse.py check-msg <commit-msg-file> <repo_root>
        -> the commit gate (called by .githooks/commit-msg): exit 1 with the reason on stderr
           if the message does not relate to the ledger. It reads trailers with the SAME
           function the sync uses, so what the gate accepts is exactly what gets recorded.

Entry ids are content hashes (`F-3fa9c1e`), never a counter: the same text gets the same id on
every clone and branch, so two machines or two branches can never hand out one id twice, and a
rebase or squash-merge leaves the id intact. Legacy sequential ids (`F-014`) are still parsed
everywhere and never renumbered.

ledger-template-version: 4
"""
import datetime
import hashlib
import io
import os
import re
import sys

# An entry id: a 7-hex content hash, or a legacy 3-6 digit sequence number.
ID_PAT = r'[A-Z]-(?:[0-9a-f]{7}|\d{3,6})'
ID_RE = re.compile(r'\b(' + ID_PAT + r')\b')
# The header is parsed leniently (any non-space id) so an odd hand-written entry is still an
# entry; `lint` is what reports it.
HDR = re.compile(r'^## (\S+) · (\S+) · (\S+) · (\S+) · (\d{4}-\d{2}-\d{2})(.*)$')
ENTRIES_MARKER = '<!-- ENTRIES_START -->'
STATUSES = ('OPEN', 'CLOSED', 'SUPERSEDED', 'STANDING')
TYPES = ('decision', 'finding', 'action', 'retired', 'note', 'thought')


# --- ids -----------------------------------------------------------------------

def norm_text(text):
    """Whitespace-collapsed, case-preserved text used for hashing and dedup."""
    return ' '.join(text.split())


def hash_id(prefix, text):
    """Content-hash id: <prefix>-<7 hex of sha1('<prefix>:' + normalized text)>."""
    h = hashlib.sha1(f'{prefix}:{norm_text(text)}'.encode('utf-8')).hexdigest()
    return f'{prefix}-{h[:7]}'


def is_legacy(eid):
    return re.fullmatch(r'[A-Z]-\d{3,6}', eid) is not None


# --- trailers ------------------------------------------------------------------

# Trailers that create an entry: key -> (id prefix, type, initial status)
KINDS = {
    'Decision': ('D', 'decision', 'CLOSED'),     # a settled choice, in force until superseded
    'Finding':  ('F', 'finding',  'STANDING'),   # a settled fact / result — nothing left to do
    'Opens':    ('F', 'finding',  'OPEN'),       # an open problem or question — something to resolve
    'Fixed':    ('F', 'finding',  'CLOSED'),     # a problem found AND resolved in this commit
    'Action':   ('A', 'action',   'OPEN'),       # a to-do; pair with `Due:` / `Owner:`
    'Retires':  ('R', 'retired',  'STANDING'),   # an approach that is now dead — never re-propose
}
RELATE_KEYS = ('Closes', 'Supersedes', 'Refs')   # act on existing entries by id
# Modifiers shape the entry trailer directly above them (or, placed before the first entry
# trailer, every entry of the commit). They never satisfy the commit gate on their own.
MODIFIER_KEYS = ('Due', 'Owner', 'Area', 'Pin', 'Date')
LORE_KEYS = ('Rejected', 'Constraint', 'Directive', 'Confidence', 'Scope-risk',
             'Reversibility', 'Tested', 'Not-tested', 'Related')
# `Tidy: <summary>` marks a /ledger-tidy pass: it creates nothing, and the next "tidy due"
# check counts work from it.
LEDGER_KEYS = tuple(KINDS) + RELATE_KEYS + ('Ledger', 'Tidy')
# Keys whose value may be wrapped onto following unindented lines.
WRAPPABLE = tuple(KINDS) + RELATE_KEYS + MODIFIER_KEYS + LORE_KEYS + ('Ledger', 'Tidy')


def supersede_ids(val):
    """`Supersedes: D-a, D-b by D-c` -> (['D-a', 'D-b'], ['D-c']). No `by`: (ids, [])."""
    left, sep, right = val.partition(' by ')
    return ID_RE.findall(left), (ID_RE.findall(right) if sep else [])

TOKEN_LINE = re.compile(r'^([A-Za-z][\w-]*):[ \t]+\S')


def _trailer_para(lines):
    """-> the paragraph's logical trailer lines, or None if it is not a trailer paragraph.

    A trailer paragraph starts with a `Token: value` line. Every other line is another
    `Token: value`, or a CONTINUATION of the one before it: indented (git's own rule), or, after
    a ledger/Lore key, simply wrapped — agents wrap long trailers at 72 columns, and a wrapped
    trailer must not take the whole block down with it.
    """
    if not lines or not TOKEN_LINE.match(lines[0]):
        return None
    out, key = [], None
    for raw in lines:
        m = TOKEN_LINE.match(raw)
        if m:
            key = m.group(1)
            out.append(raw.strip())
        elif raw[:1] in (' ', '\t') or key in WRAPPABLE:
            out[-1] = out[-1] + ' ' + raw.strip()
        else:
            return None
    return out


def trailer_lines(body):
    """The trailing trailer block of a commit message, as logical (unwrapped) lines.

    Walk paragraphs from the end, keeping each while it is a trailer paragraph. Prose that
    says "Decision:" mid-paragraph is never a trailer; neither is anything above the block.
    """
    paras = re.split(r'\n[ \t]*\n', body.strip())
    out = []
    for para in reversed(paras):
        got = _trailer_para([l.rstrip() for l in para.splitlines() if l.strip()])
        if got is None:
            break
        out = got + out
    return out


def stray_ledger_lines(body):
    """Ledger-keyed lines OUTSIDE the trailing block: they would be silently ignored."""
    paras = re.split(r'\n[ \t]*\n', body.strip())
    n = len(paras)
    kept = 0
    for para in reversed(paras):
        if _trailer_para([l.rstrip() for l in para.splitlines() if l.strip()]) is None:
            break
        kept += 1
    out = []
    for para in paras[1:n - kept]:          # never the subject paragraph
        for l in para.splitlines():
            m = TOKEN_LINE.match(l.strip())
            if m and m.group(1) in LEDGER_KEYS:
                out.append(l.strip())
    return out


# --- ledger file ---------------------------------------------------------------

def split_ledger(text):
    """-> (preamble up to and including the marker, entries text after it)."""
    if ENTRIES_MARKER in text:
        head, tail = text.split(ENTRIES_MARKER, 1)
        return head + ENTRIES_MARKER, tail
    return text, ''


def parse_blocks(entries_text):
    """Parse the entries section into dicts, in file order (newest first).

    Each dict: id, status, type, ws, date, suffix, lines (body lines, stripped), raw (the exact
    block, header included, no trailing blank lines). Text before the first header is returned
    as a pseudo-entry with id None so a round-trip never loses it.
    """
    entries, cur, raw, lead = [], None, [], []
    for line in entries_text.splitlines():
        m = HDR.match(line)
        if m:
            if cur is not None:
                cur['raw'] = '\n'.join(raw).rstrip()
            cur = dict(id=m.group(1), status=m.group(2), type=m.group(3), ws=m.group(4),
                       date=m.group(5), suffix=m.group(6), lines=[], raw='')
            raw = [line]
            entries.append(cur)
        elif cur is not None:
            raw.append(line)
            if line.strip() and not line.startswith('<!--'):
                cur['lines'].append(line.strip())
        else:
            lead.append(line)
    if cur is not None:
        cur['raw'] = '\n'.join(raw).rstrip()
    lead_text = '\n'.join(lead).strip()
    if lead_text:
        entries.insert(0, dict(id=None, status='', type='', ws='', date='', suffix='',
                               lines=[], raw=lead_text))
    return entries


def parse_entries(path):
    try:
        text = io.open(path, encoding='utf-8').read()
    except OSError:
        return []
    return [e for e in parse_blocks(split_ledger(text)[1]) if e['id']]


def insert_by_date(text, block):
    """Insert an entry block above the first existing entry dated on or before it, so the
    file stays newest-first even when older entries arrive late (a merge, a recovery). The
    order of existing entries is never rewritten. Blocks inserted oldest-first land in order."""
    m = HDR.match(block.split('\n', 1)[0])
    date = m.group(5) if m else '9999-99-99'
    head, sep, tail = text.partition(ENTRIES_MARKER)
    if not sep:
        return text
    pos = None
    for hm in re.finditer(r'^## .*$', tail, re.M):
        h = HDR.match(hm.group(0))
        if h and h.group(5) <= date:
            pos = hm.start()
            break
    block = block.rstrip('\n') + '\n'
    if pos is None:
        tail = tail.rstrip('\n') + '\n\n' + block
    else:
        tail = tail[:pos] + block + '\n' + tail[pos:]
    if not tail.startswith('\n\n'):
        tail = '\n\n' + tail.lstrip('\n')
    return head + sep + tail


def entry_text(e):
    """The entry's one-line statement: its first body line that is not a pointer/annotation."""
    return next((l for l in e['lines'] if l[:1] not in ('→', '·', '↔', '✓', '⤳')), '')


def entry_commit(e):
    for l in e['lines']:
        m = re.match(r'^→ commit ([0-9a-f]{4,})', l)
        if m:
            return m.group(1)
    return ''


def entry_meta(e, key):
    """Value of a `· Key: value` body line ('' if absent)."""
    pre = f'· {key}: '
    return next((l[len(pre):].strip() for l in e['lines'] if l.startswith(pre)), '')


def entry_due(e):
    v = entry_meta(e, 'Due')
    return v if re.fullmatch(r'\d{4}-\d{2}-\d{2}', v) else ''


def entry_pinned(e):
    return any(l == '· Pinned' for l in e['lines'])


# --- rendering helpers ----------------------------------------------------------

def _one(e, width=200, dated=False):
    ws = '' if e['ws'] == '-' else f" [{e['ws']}]"
    tags = []
    if entry_due(e):
        tags.append(f"due {entry_due(e)}")
    if entry_meta(e, 'Owner'):
        tags.append(f"owner {entry_meta(e, 'Owner')}")
    if entry_meta(e, 'Confidence').lower().startswith('low'):
        tags.append('low confidence')
    if entry_meta(e, 'Reversibility').lower().startswith('irreversible'):
        tags.append('irreversible')
    if dated:
        tags.append(e['date'])
    tag = f" ({', '.join(tags)})" if tags else ''
    return f"- {e['id']}{ws}{tag} {entry_text(e)}"[:width]


def _short(e, width=70):
    return f"{e['id']} {entry_text(e)}"[:width].rstrip()


def _tilde(p):
    home = os.path.expanduser('~')
    return '~' + p[len(home):] if home and p.startswith(home + os.sep) else p


def _recent_areas(paths_file):
    """Every leading path segment (a, a/b, a/b/c) of recently touched files."""
    areas = set()
    if not paths_file:
        return areas
    try:
        for line in io.open(paths_file, encoding='utf-8'):
            parts = [p for p in line.strip().split('/')[:-1] if p]
            for i in range(1, len(parts) + 1):
                areas.add('/'.join(parts[:i]))
                areas.add(parts[i - 1])
    except OSError:
        pass
    return areas


# --- commands --------------------------------------------------------------------

def _by_area(items, areas):
    """Stable partition: entries in a recently touched area first, file order within."""
    return [e for e in items if e['ws'] in areas] + [e for e in items if e['ws'] not in areas]


def cmd_digest(argv):
    # argv[1] (a cutoff date) is accepted for compatibility and no longer used: "recent" is
    # the newest entries, so the section survives a two-week break instead of going empty.
    path, max_open, max_recent = argv[0], int(argv[2]), int(argv[3])
    areas = _recent_areas(argv[4] if len(argv) > 4 else '')
    today = datetime.date.today().isoformat()
    entries = parse_entries(path)
    if not entries:
        return ''
    disp = _tilde(os.environ.get('LL_DISPLAY_PATH') or path)
    live = [e for e in entries if not is_dead(e)]
    openi = [e for e in entries if e['status'] == 'OPEN']
    overdue = sorted((e for e in openi if entry_due(e) and entry_due(e) <= today), key=entry_due)
    openi = overdue + _by_area([e for e in openi if e not in overdue], areas)
    pinned = [e for e in live if entry_pinned(e)]
    retire = _by_area([e for e in live if e['type'] == 'retired' and not entry_pinned(e)], areas)
    recent = [e for e in live if not entry_pinned(e) and (
        e['type'] == 'decision' or (e['type'] == 'finding' and e['status'] == 'STANDING'))]
    recent = _by_area(recent[:max_recent * 2], areas)[:max_recent]

    out = ["# Project ledger digest (auto-injected; full file: %s)" % disp, ""]
    out.append(f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} · {len(openi)} open"
               + (f" ({len(overdue)} overdue)" if overdue else "")
               + f" · {len(pinned)} pinned · {len(retire)} retired framings")
    out.append("")
    if openi:
        out.append(f"## Open ({len(openi)}) — problems, questions, actions; do not re-discover these")
        out += [_one(e) for e in openi[:max_open]]
        if len(openi) > max_open:
            out.append(f"- …{len(openi) - max_open} more: grep '· OPEN ·' {disp} "
                       f"— over the digest cap; triage (close, supersede, or demote)")
        out.append("")
    if pinned:
        out.append(f"## Pinned — in force; check before contradicting ({len(pinned)})")
        out += [_one(e, dated=True) for e in pinned[:12]]
        if len(pinned) > 12:
            out.append(f"- …{len(pinned) - 12} more: grep -B2 '^· Pinned' {disp}")
        out.append("")
    if recent:
        out.append("## Recently decided / established")
        out += [_one(e) for e in recent]
        out.append("")
    if retire:
        out.append(f"## Retired — settled; do NOT re-propose ({len(retire)})")
        out += [_one(e, width=160) for e in retire[:16]]
        if len(retire) > 16:
            out.append(f"- …{len(retire) - 16} more: grep '· retired ·' {disp}")
        out.append("")
    out.append("Every commit carries a ledger trailer — Decision: · Finding: (a fact) · Opens: (a "
               "problem) · Fixed: · Action: · Retires: · Closes:/Supersedes:/Refs: <id> — or "
               "`Ledger: none — <reason>`. A decision with no file change is an empty commit "
               "(`git commit --allow-empty --only`).")
    out.append("If something above looks wrong or stale, say so — do not silently work around it.")
    return "\n".join(out)


def is_dead(e):
    """No longer true / no longer in force. A decision is born CLOSED (a closed question) and
    stays in force, so only SUPERSEDED kills it; a finding or action dies when it is CLOSED."""
    if e['status'] == 'SUPERSEDED':
        return True
    return e['status'] == 'CLOSED' and e['type'] in ('finding', 'action')


def stale_rules(ledger_path, rules_dir):
    """Rules that outlived the entry they were written for.

    A path-scoped rule exists to stop one open finding being re-derived, or to hold a decision
    in force. Once the finding is closed or the decision superseded, the rule is telling future
    sessions something no longer true.
    """
    status = {e['id']: (e['status'] if is_dead(e) else '') for e in parse_entries(ledger_path)}
    out = []
    try:
        names = sorted(os.listdir(rules_dir))
    except OSError:
        return out
    for name in names:
        if not name.endswith('.md') or name == 'README.md':
            continue
        try:
            text = io.open(os.path.join(rules_dir, name), encoding='utf-8').read()
        except OSError:
            continue
        seen = []
        for line in text.splitlines():
            # a line that itself says the entry is closed ("F-005 · CLOSED — do not re-raise")
            # cites it on purpose, as a settled warning: that is not stale
            if re.search(r'(?i)\b(closed|superseded|resolved|retired)\b', line):
                continue
            for eid in ID_RE.findall(line):
                st = status.get(eid)
                if st and eid not in seen:
                    seen.append(eid)
                    out.append('stale rule: %s cites %s (%s)' % (name, eid, st))
    return out


def cmd_stale_rules(argv):
    return "\n".join(stale_rules(argv[0], argv[1]))


def lint(path):
    """Defects that would otherwise be skipped silently by every reader of the ledger."""
    try:
        text = io.open(path, encoding='utf-8').read()
    except OSError:
        return []
    out = []
    if ENTRIES_MARKER not in text:
        return [f"no {ENTRIES_MARKER} marker — the sync has nowhere to insert entries"]
    pre, ent = split_ledger(text)
    base = pre.count('\n') + 1
    seen = {}
    for i, line in enumerate(ent.splitlines()):
        if not line.startswith('## '):
            continue
        n = base + i
        m = HDR.match(line)
        if not m:
            out.append(f"line {n}: header not in `## ID · STATUS · type · area · YYYY-MM-DD` form "
                       f"— invisible to the digest: {line[:80]}")
            continue
        eid, st, typ = m.group(1), m.group(2), m.group(3)
        if not re.fullmatch(ID_PAT, eid):
            out.append(f"line {n}: id {eid} is neither a content hash (X-1a2b3c4) nor legacy (X-014)")
        if st not in STATUSES:
            out.append(f"line {n}: {eid} has unknown status {st}")
        if typ not in TYPES:
            out.append(f"line {n}: {eid} has unknown type {typ}")
        if eid in seen:
            out.append(f"line {n}: duplicate id {eid} (first at line {seen[eid]})")
        else:
            seen[eid] = n
    return out


def cmd_lint(argv):
    return "\n".join(lint(argv[0]))


def cmd_block(argv):
    path, repo_id, repo_path, head, state, since = argv[:6]
    version = argv[6] if len(argv) > 6 else ''
    max_open = int(argv[7]) if len(argv) > 7 else 22
    stale = stale_rules(path, os.path.join(repo_path, '.claude', 'rules'))
    tidy = tidy_status(path, repo_path, max_open) if os.path.isdir(repo_path) else ''
    repo_abs = repo_path
    repo_path = _tilde(repo_path)
    today = datetime.date.today().isoformat()
    entries = parse_entries(path)  # file order == newest first
    openi = [e for e in entries if e['status'] == 'OPEN']
    overdue = [e for e in openi if entry_due(e) and entry_due(e) <= today]
    recent = [e for e in entries if e['type'] == 'decision' and e['status'] != 'SUPERSEDED'][:3]

    flags = []
    if state and state != 'clean':
        flags.append(state)
    try:
        n = int(since)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        what = "since last entry" if entries else "unrecorded"
        flags.append(f"{n} commit{'s' if n != 1 else ''} {what}")
    if overdue:
        flags.append(f"⏰ {len(overdue)} overdue")
    if len(openi) > max_open:
        flags.append(f"⚠ triage: {len(openi)} open > digest cap {max_open}")
    if tidy:
        flags.append("🧹 tidy due")

    if os.path.isdir(repo_abs):
        st = share_state(repo_abs)
        if st['unpushed']:
            flags.append(f"⇡ {st['unpushed']} records unpushed")
        if st['incoming']:
            flags.append(f"⇣ {st['incoming']} records not pulled")
        live = [o for o in st['others'] if not o['stale']]
        if live:
            flags.append('unmerged: ' + ', '.join(f"{o['branch']} ({o['records']})" for o in live[:3]))
    host = os.environ.get('LL_HOST', '')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    ver = f" · template v{version}" if version else ""
    out = [f"<!-- REPO:{repo_id} START -->",
           f"## {repo_id}  ·  {repo_path}",
           f"_rebuilt {stamp}_",
           f"HEAD {head or '?'}" + (f" on {host}" if host else "") + " · "
           + (" · ".join(flags) if flags else "up to date")
           + f" · {len(openi)} open · {len(entries)} entries{ver}"]
    if overdue:
        out.append("**Overdue:** " + " · ".join(f"{_short(e, 60)} (due {entry_due(e)})"
                                                for e in overdue[:6]))
    if openi:
        shown = " · ".join(_short(e) for e in openi[:12])
        more = f" · …+{len(openi) - 12}" if len(openi) > 12 else ""
        out.append(f"**Open ({len(openi)}):** {shown}{more}")
    if recent:
        out.append("**Recent decisions:** " + " · ".join(_short(e) for e in recent))
    if stale:
        out.append("**Stale rules (%d):** " % len(stale)
                   + " · ".join(x[len('stale rule: '):] for x in stale[:6]))
    if not entries:
        out.append("_no entries yet — tracking forward from install_")
    out.append(f"<!-- REPO:{repo_id} END -->")
    return "\n".join(out)


def _conf(root, key):
    try:
        for line in io.open(os.path.join(root, '.claude', 'ledger.conf'), encoding='utf-8'):
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip()
    except OSError:
        pass
    return ''


def external_prefixes(root):
    """Id prefixes that belong to ANOTHER register (EXTERNAL_IDS=C in ledger.conf, e.g. a
    CONCERNS.md numbered C-001…): trailers may cite them, the ledger never owns them."""
    return {p for p in re.split(r'[\s,]+', _conf(root, 'EXTERNAL_IDS').strip('"\'')) if p}


def _ledger_path(root):
    rel = _conf(root, 'LEDGER_PATH')
    if rel:
        return os.path.join(root, rel)
    for c in ('docs_root/LEDGER.md', 'docs/LEDGER.md', 'LEDGER.md'):
        if os.path.exists(os.path.join(root, c)):
            return os.path.join(root, c)
    return ''


GATE_HELP = """  Add one line at the end of the message, after a blank line — for example:

    Decision: use a single-writer queue instead of a lock
    Finding:  the vendor API caps requests at 10/s          (a settled fact)
    Opens:    the cache is never invalidated after a deploy  (a problem to resolve)
    Ledger:   none — pure whitespace reformat, no behaviour change

  (Claude normally writes these for you. More kinds — Fixed:, Action:, Retires:, Closes:,
  Supersedes:, Refs: — are listed at the top of the ledger. A decision with no file change:
  git commit --allow-empty --only -m "…" -m "Decision: …".  Escape hatch: --no-verify)
"""
ID_LED = re.compile(r'^[A-Z]{1,3}-[0-9A-Za-z]+\b')


def check_msg(msgfile, root):
    """-> list of problems (empty = the commit may proceed)."""
    if os.environ.get('LEDGER_SKIP', '').lower() in ('1', 'true', 'yes') \
            or os.environ.get('LEDGER_SYNC_IN_PROGRESS'):
        return []
    try:
        raw = io.open(msgfile, encoding='utf-8', errors='replace').read()
    except OSError:
        return []
    kept = []
    for line in raw.splitlines():
        if re.match(r'^[#;]?\s*-+ >8 -+', line):
            break                                   # `git commit -v` scissors
        if not line.startswith('#'):
            kept.append(line)
    import subprocess
    author = subprocess.run(['git', 'var', 'GIT_AUTHOR_IDENT'], capture_output=True, text=True,
                            cwd=root).stdout
    return check_body('\n'.join(kept).strip(), root, author)


def check_body(body, root, author, structural_only=False):
    """The gate's rules on one message. structural_only (for `ledger validate` over past
    commits): skip the checks that depend on the ledger as it is NOW (unknown ids, duplicates)."""
    if not body:
        return []                                   # git aborts an empty message itself
    subject = body.splitlines()[0].strip()
    if re.match(r'^(Merge |merge: |Revert |Revert: |revert: |fixup! |squash! |amend! |chore: ledger sync)', subject):
        return []
    if '[bot]' in author:
        return []
    for key, target in (('EXEMPT_SUBJECTS', subject), ('EXEMPT_AUTHORS', author)):
        pat = _conf(root, key).strip('"\'')
        if pat:
            try:
                if re.search(pat, target):
                    return []
            except re.error:
                pass

    problems = []
    stray = stray_ledger_lines(body)
    if stray:
        problems.append("these ledger lines are NOT in the final trailer paragraph, so they would be "
                        "silently ignored:\n" + '\n'.join(f'      {l}' for l in stray)
                        + "\n    Move them into the last paragraph (no prose after them).")
    tl = trailer_lines(body)
    ledger = '' if structural_only else _ledger_path(root)
    try:
        known = {e['id'] for e in parse_entries(ledger)} if ledger else set()
    except Exception:
        known = set()
    declared = {m.group(1) for l in tl for m in [re.match(r'^Opens:\s+(F-\S+)\s+\S', l)] if m}
    ext = external_prefixes(root)
    related = entries = False
    for line in tl:
        key, _, val = line.partition(':')
        val = val.strip()
        if key in KINDS:
            lead = ID_LED.match(val)
            if key == 'Opens' and re.match(r'^F-(?:[0-9a-f]{7}|\d{3,6})\s+\S', val):
                entries = True
            elif lead and lead.group(0).split('-')[0] in ext and len(val[lead.end():].split()) >= 3:
                entries = True        # "Opens: C-032 -- <words>": cites the other register, has content
            elif lead:
                problems.append(f"`{key}: {val[:50]}` starts with an id. To relate this commit to an "
                                f"existing entry use `Refs: <id>` (or `Closes:`); `{key}:` records a "
                                f"new statement in words.")
            else:
                entries = True
        elif key == 'Tidy':
            related = True
        elif key in RELATE_KEYS:
            ids = ID_RE.findall(val)
            if not ids:
                problems.append(f"`{key}: {val[:40]}` names no entry id (ids look like F-3fa9c1e or F-014).")
            for i in ids:
                if i.split('-')[0] in ext:
                    continue              # an id of the other register: not the ledger's to check
                if ledger and os.path.exists(ledger) and i not in known and i not in declared:
                    problems.append(f"`{key}: {i}` — there is no entry {i} in "
                                    f"{os.path.relpath(ledger, root)}. (On another branch? Merge it "
                                    f"first, or use --no-verify.)")
            related = related or bool(ids)
        elif key == 'Ledger':
            m = re.match(r'^none\b[\s\W]*(.*)$', val, re.I)
            if m and len(m.group(1).split()) >= 3:
                related = True
            else:
                problems.append("`Ledger:` is only accepted as `Ledger: none — <reason, at least 3 "
                                "words>`.")
        elif key in ('Due', 'Date') and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', val):
            problems.append(f"`{key}: {val}` must be a date, YYYY-MM-DD.")
    # a new entry that reads like a live one is how duplicates are born (the planned decision,
    # then the same decision again when enacted): the commit must say how they relate
    if entries and ledger and os.path.exists(ledger) and not any(
            l.startswith('Tidy:') for l in tl):
        mentioned = set()
        for line in tl:
            k, _, v = line.partition(':')
            if k in RELATE_KEYS:
                mentioned.update(ID_RE.findall(v))
        live = [e for e in parse_entries(ledger) if not is_dead(e)]
        for line in tl:
            k, _, v = line.partition(':')
            if k not in KINDS or not v.strip():
                continue
            typ = KINDS[k][1]
            tv = _toks(v)
            # anti-pattern filtering: a decision that re-adopts a retired framing, or an
            # alternative a live decision rejected, must say so on purpose
            if typ == 'decision':
                hit = None
                for e in live:
                    if e['id'] in mentioned:
                        continue
                    if e['type'] == 'retired' and similar(tv, _toks(entry_text(e))):
                        hit = (e, 'retired', entry_text(e))
                        break
                    for l in e['lines']:
                        if l.startswith('· Rejected:'):
                            alt = l[len('· Rejected:'):].split('|')[0].strip()
                            if similar(tv, _toks(alt)):
                                hit = (e, 'rejected', l[2:])
                                break
                    if hit:
                        break
                if hit:
                    e, how, what = hit
                    problems.append(
                        f"`{k}: {v.strip()[:60]}` re-adopts what {e['id']} "
                        + ("retired" if how == 'retired' else "rejected")
                        + f" (\"{what[:80]}\"). If that is deliberate, add `Supersedes: {e['id']}` and say "
                        f"in the body what changed; otherwise this is the dead end the ledger exists to prevent.")
                    continue
            for e in live:
                if e['type'] == typ and e['id'] not in mentioned and similar(tv, _toks(entry_text(e))):
                    problems.append(
                        f"`{k}: {v.strip()[:60]}` reads like {e['id']} (\"{entry_text(e)[:70]}\"). "
                        f"If this commit enacts or extends it: `Refs: {e['id']}` instead of a new entry. "
                        f"If it replaces it: keep the entry and add `Supersedes: {e['id']}`. If it is "
                        f"genuinely different: keep the entry and add `Refs: {e['id']}` to show you checked.")
                    break

    if not problems and not (entries or related):
        problems.append("this commit does not relate to the ledger.")
    return problems


def cmd_check_msg(argv):
    problems = check_msg(argv[0], argv[1] if len(argv) > 1 else os.getcwd())
    if problems:
        sys.stderr.write("\nliving-ledger: commit REJECTED — " + problems[0] + "\n")
        for p in problems[1:]:
            sys.stderr.write("  also: " + p + "\n")
        sys.stderr.write("\n" + GATE_HELP + "\n")
        sys.exit(1)
    return ''


# --- tidy: when it is due, and what it should look at -----------------------------

def _git(root, *args):
    import subprocess
    return subprocess.run(['git', '-C', root, *args], capture_output=True, text=True).stdout


def _counted_commits(root, rev_range):
    """Commits that are real work: no merges, no ledger sync commits, no [bot] authors, nothing
    matching EXEMPT_SUBJECTS / EXEMPT_AUTHORS."""
    exs = _conf(root, 'EXEMPT_SUBJECTS').strip('"\'')
    exa = _conf(root, 'EXEMPT_AUTHORS').strip('"\'')
    n = 0
    for line in _git(root, 'log', '--no-merges', '--format=%an <%ae>%x09%s', rev_range).splitlines():
        who, _, subj = line.partition('\t')
        if re.match(r'(chore|docs)(\(ledger\))?: (ledger|sync ledger)', subj) or '[bot]' in who:
            continue
        try:
            if (exs and re.search(exs, subj)) or (exa and re.search(exa, who)):
                continue
        except re.error:
            pass
        n += 1
    return n


def tidy_baseline(ledger, root):
    """-> (sha, date, label): the last `Tidy:` commit, else the commit that created the ledger."""
    last = _git(root, 'log', '-1', '--format=%H%x09%cs', '--grep=^Tidy:', 'HEAD').strip()
    if last:
        sha, date = last.split('\t')
        return sha, date, f'the last tidy ({date})'
    # the ledger may be read from a temp copy (the digest's view): git history needs the real path
    rel = _conf(root, 'LEDGER_PATH') or os.path.relpath(ledger, root)
    born = _git(root, 'log', '--diff-filter=A', '--format=%H%x09%cs', 'HEAD', '--', rel).strip()
    if born:
        sha, date = born.splitlines()[-1].split('\t')
        return sha, date, f'the ledger began ({date}; never tidied)'
    return '', '', ''


def tidy_status(ledger, root, max_open=22):
    """Why a tidy is due, or ''. Volume of work and time both count: a quiet month is not due,
    a one-day burst of fifty entries is; a busy fortnight is. Thresholds: TIDY_MIN_DAYS (7) and
    TIDY_VOLUME (25) in ledger.conf, where volume = new entries + 3 x merges + commits / 5."""
    entries = parse_entries(ledger)
    if not entries:
        return ''
    sha, base_date, label = tidy_baseline(ledger, root)
    if not sha:
        return ''
    tidied = label.startswith('the last tidy')
    new = [e for e in entries if (e['date'] > base_date if tidied else e['date'] >= base_date)
           or not tidied]
    commits = _counted_commits(root, f'{sha}..HEAD')
    merges = len(_git(root, 'rev-list', '--merges', f'{sha}..HEAD').split())
    days = (datetime.date.today() - datetime.date.fromisoformat(base_date)).days
    openi = sum(1 for e in entries if e['status'] == 'OPEN')
    try:
        min_days = int(_conf(root, 'TIDY_MIN_DAYS') or 7)
        vol_need = int(_conf(root, 'TIDY_VOLUME') or 25)
    except ValueError:
        min_days, vol_need = 7, 25
    volume = len(new) + 3 * merges + commits // 5
    # an open list over the digest cap is a reason too — but not the day after a tidy that
    # reviewed it: it waits for TIDY_MIN_DAYS like everything else
    due = ((days >= min_days and volume >= vol_need) or volume >= 3 * vol_need
           or (openi > max_open and days >= min_days))
    if not due:
        return ''
    why = (f"{len(new)} new entries, {merges} merge{'s' if merges != 1 else ''} and "
           f"{commits} commit{'s' if commits != 1 else ''} over {days} day{'s' if days != 1 else ''} "
           f"since {label}")
    if openi > max_open:
        why += f"; {openi} open items, over the digest cap of {max_open}"
    st = share_state(root)
    first = []
    if st['default'] and st['branch'] != st['default']:
        first.append(f"switch to {st['default']} (tidy the default branch)")
    if st['incoming']:
        first.append(f"pull {st['incoming']} records from {st['upstream']}")
    live_branches = [o for o in st['others'] if not o['stale']]
    if live_branches:
        first.append('merge ' + ', '.join(f"{o['branch']} ({o['records']})" for o in live_branches[:3])
                     + ' — or tidy knowing those arrive later')
    if first:
        why += '. Before tidying: ' + '; '.join(first)
    return why


def cmd_tidy_status(argv):
    return tidy_status(argv[0], argv[1], int(argv[2]) if len(argv) > 2 else 22)


STOP = set('the a an and or of to in on for with by is are be as at from that this it its not no '
           'into than then when only each every one two all any must should can will was were has '
           'have had but so if per via use used uses using new now also more less same'.split())


def _toks(text):
    return {w for w in re.findall(r'[a-z0-9_]{3,}', text.lower()) if w not in STOP}


def similar(ta, tb):
    """Near-duplicate test on two token sets: Jaccard catches rewordings, the overlap
    coefficient a short restatement of a longer entry (only on entries long enough not to match
    by accident). Measured on real ledgers: no false positives at these settings."""
    if len(ta) < 3 or len(tb) < 3:
        return False
    shared = len(ta & tb)
    return (shared / len(ta | tb) >= 0.3
            or (min(len(ta), len(tb)) >= 5 and shared / min(len(ta), len(tb)) >= 0.6))


_EXT_INDEX = {}


def external_status(root, eid):
    """The status line another register gives one of its ids: the first line mentioning
    `status` in the Markdown section headed by that id, or ''. All tracked .md files are indexed
    once per run (no grep dialect differences)."""
    prefixes = ''.join(sorted(external_prefixes(root)))
    key = (root, prefixes)
    if key not in _EXT_INDEX:
        idx = {}
        head = re.compile(r'^#+ .*?\b([' + (prefixes or 'Z') + r']-\d{3,6})\b')
        for path in _git(root, 'ls-files', '*.md').splitlines():
            try:
                lines = io.open(os.path.join(root, path), encoding='utf-8').read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            cur = None
            for l in lines:
                m = head.match(l)
                if m:
                    cur = m.group(1) if m.group(1) not in idx else None
                    if cur:
                        idx[cur] = ''
                    continue
                if l.startswith('#'):
                    cur = None
                elif cur and not idx[cur] and re.search(r'(?i)\bstatus\b', l):
                    idx[cur] = re.sub(r'[*_`]', '', l).strip(' -')[:140]
        _EXT_INDEX[key] = idx
    return _EXT_INDEX[key].get(eid, '')


def tidy_report(ledger, root, max_open=22):
    """The candidates a tidy pass reviews. Heuristics only — every item is a proposal for the
    user to accept, edit or decline, never an automatic change."""
    entries = parse_entries(ledger)
    today = datetime.date.today()
    out = []
    status = tidy_status(ledger, root, max_open)
    sha, base_date, label = tidy_baseline(ledger, root)
    live = [e for e in entries if not is_dead(e)]
    openi = [e for e in entries if e['status'] == 'OPEN']
    by_commit = {}
    for e in entries:
        by_commit.setdefault(entry_commit(e), []).append(e)

    def line(e, why, act, width=90):
        return f"- {e['id']} · {entry_text(e)[:width]} — {why} → {act}"

    def section(title, items, cap=25):
        if items:
            out.append(f"## {title} ({len(items)})")
            out.extend(items[:cap])
            if len(items) > cap:
                out.append(f"- …{len(items) - cap} more")
            out.append('')

    out.append(f"# Ledger tidy report — {_conf(root, 'LEDGER_PATH') or os.path.relpath(ledger, root)}")
    out.append('')
    out.append(f"{len(entries)} entries · {len(live)} live · {len(openi)} open · since {label or 'n/a'}"
               + (f" · due: {status}" if status else " · not due yet"))
    out.append('')

    # 1. open items that look settled
    settled = []
    fact = re.compile(r'\b(fixed|resolved|reproduc\w*|confirm\w*|refuted|verified|no change (is )?needed|'
                      r'now (passes|works)|measured|holds|is correct)\b', re.I)
    for e in openi:
        c = entry_commit(e)
        partners = [x['id'] for x in by_commit.get(c, []) if x is not e and x['type'] == 'decision'] if c else []
        subj = _git(root, 'show', '-s', '--format=%s', c).strip() if c else ''
        if fact.search(entry_text(e)):
            settled.append(line(e, 'reads like a settled result', 'STANDING (a fact) or CLOSED'))
        elif partners:
            settled.append(line(e, f'opened in the same commit as {", ".join(partners)}',
                                'CLOSED if that decision resolved it'))
        elif re.match(r'fix\b|fix[(:]', subj):
            settled.append(line(e, f'born in a fix commit ({c})', 'CLOSED if the fix covers it'))
    section('Open, but possibly settled', settled)

    # 2. overdue and aging open items
    aging = []
    for e in openi:
        due = entry_due(e)
        age = (today - datetime.date.fromisoformat(e['date'])).days
        if due and due <= today.isoformat():
            aging.append(line(e, f'overdue since {due}', 'close, re-date (new Due:) or drop'))
        elif age > 30 and not any(l[:1] in ('↔', '✓') for l in e['lines']):
            aging.append(line(e, f'open {age} days, never referenced since', 'still real? close or keep'))
    section('Overdue or aging', aging)

    # 3. near-duplicates and possible contradictions among live entries of one type
    dups = []
    for typ in ('decision', 'finding', 'action', 'retired'):
        group = [(e, _toks(entry_text(e))) for e in live if e['type'] == typ]
        for i in range(len(group)):
            a, ta = group[i]
            if len(ta) < 3:
                continue
            for b, tb in group[i + 1:]:
                if similar(ta, tb):
                    dups.append(f"- {a['id']} ≈ {b['id']} · {entry_text(a)[:60]} | {entry_text(b)[:60]}"
                                f" → same thing (Supersedes: <older> by <newer>), a refinement, or a"
                                f" contradiction to settle")
    section('Similar entries — duplicate, refinement or contradiction?', dups)

    # 3b. open items that mirror another register's item: show its status there
    ext = external_prefixes(root)
    mirrors = []
    shut = re.compile(r'(?i)^status\.?\s*:?\s*(closed|resolved|fixed|done|withdrawn)\b')
    for e in openi:
        cited = list(dict.fromkeys(i for i in ID_RE.findall(entry_text(e)) if i.split('-')[0] in ext))
        states = [(c, external_status(root, c)) for c in cited]
        states = [(c, 'closed' if shut.search(st) else 'open') for c, st in states if st]
        if not states:
            continue
        closed = [c for c, st in states if st == 'closed']
        summary = ', '.join(f'{c} {st}' for c, st in states)
        if len(closed) == len(states):
            act = 'CLOSE here too'
        elif closed:
            act = f'keep ({len(closed)} of {len(states)} closed there — reword to what is still open?)'
        else:
            act = 'still open there — keep'
        mirrors.append(line(e, summary, act, width=60))
    section('Mirrors of another register — its status beside the ledger\'s', mirrors, cap=30)

    # 4. entries with no content of their own
    junk = [line(e, 'no content of its own', 'Supersedes: <it> by <the real entry>, or reword by hand')
            for e in live if len(entry_text(e).split()) <= 2 or re.fullmatch(ID_PAT, entry_text(e).strip())]
    section('Empty or id-only entries', junk)

    # 5. pinned entries worth re-confirming
    pins = [line(e, f'pinned since {e["date"]}', 'still in force? keep, or unpin (delete the · Pinned line)')
            for e in live if entry_pinned(e)
            and (today - datetime.date.fromisoformat(e['date'])).days > 60]
    section('Pinned for over 60 days', pins)

    # 6. recent decisions whose reasoning was never written down
    dec_rel = _conf(root, 'DECISIONS_PATH')
    try:
        dec_text = io.open(os.path.join(root, dec_rel), encoding='utf-8').read() if dec_rel else ''
    except OSError:
        dec_text = ''
    thin = []
    for e in live:
        if e['type'] != 'decision' or not base_date or e['date'] < base_date or e['id'] in dec_text.split(
                '<!-- DECISIONS_LOG_START -->')[0]:
            continue
        c = entry_commit(e)
        if not c:
            continue                      # hand-written / backfilled: its evidence is elsewhere
        body = _git(root, 'show', '-s', '--format=%B', c)
        paras = [p for p in re.split(r'\n\s*\n', body.strip())[1:] if p.strip()]
        prose = [p for p in paras if _trailer_para([l for l in p.splitlines() if l.strip()]) is None]
        if not prose:
            thin.append(line(e, 'no reasoning in its commit body or DECISIONS.md', 'add a DECISIONS.md section, or accept as self-evident'))
    section('Decisions with no written reasoning', thin, cap=15)

    # 6b. the paper's `lore stale`, and decisions taken on low confidence
    section('Directives and constraints whose code changed a lot since', stale_directives(root, ledger), cap=15)
    lowc = [line(e, f'decided on low confidence, {(today - datetime.date.fromisoformat(e["date"])).days} days ago',
                 're-validate: raise it to a firm decision, or supersede it')
            for e in live if e['type'] == 'decision' and entry_meta(e, 'Confidence').lower().startswith('low')
            and (today - datetime.date.fromisoformat(e['date'])).days > 30]
    section('Low-confidence decisions older than 30 days', lowc)

    # 7. rules and headers
    rules = stale_rules(ledger, os.path.join(root, '.claude', 'rules'))
    section('Stale path-scoped rules', [f"- {r}" for r in rules])
    section('Lint', [f"- {x}" for x in lint(ledger)])

    # 8. other registers that can drift from this one
    led_rel = _conf(root, 'LEDGER_PATH') or os.path.relpath(ledger, root)
    others = [f for f in _git(root, 'ls-files').splitlines()
              if re.search(r'(?i)(retired|concern|decision|adr|handoff|status)[^/]*\.md$', f)
              and f not in (led_rel, dec_rel)]
    section('Other registers to keep consistent (or reduce to a pointer)', [f"- {f}" for f in others], cap=12)

    if len(out) <= 4:
        out.append("Nothing to tidy.")
    return "\n".join(out)


def cmd_tidy_report(argv):
    return tidy_report(argv[0], argv[1], int(argv[2]) if len(argv) > 2 else 22)


# --- sharing: ledger records that exist somewhere this checkout cannot see, or has not shown ---

def _records(root, *rev_range, limit=500):
    """Ledger records (entry trailers, Closes/Supersedes/Refs, Tidy) in the commits of a range."""
    raw = _git(root, 'log', f'-n{limit}', '--no-merges', '--format=%B%x1e', *rev_range)
    n = 0
    for body in raw.split('\x1e'):
        for l in trailer_lines(body):
            k = l.split(':', 1)[0]
            if k in KINDS or k in RELATE_KEYS or k == 'Tidy':
                n += 1
    return n


def _ago(seconds):
    m = int(seconds // 60)
    if m < 90:
        return f'{m} min'
    h = m // 60
    return f'{h} h' if h < 48 else f'{h // 24} days'


def share_state(root):
    """Everything is read from local refs — no network. `behind` is as of the last fetch."""
    import time
    st = dict(branch='', upstream='', ahead=0, behind=0, unpushed=0, incoming=0, fetch_age='',
              default='', others=[], dirty=0)
    st['branch'] = _git(root, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    up = _git(root, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}').strip()
    if up and '@{u}' not in up:
        st['upstream'] = up
        counts = _git(root, 'rev-list', '--left-right', '--count', f'{up}...HEAD').split()
        if len(counts) == 2:
            st['behind'], st['ahead'] = int(counts[0]), int(counts[1])
        if st['ahead']:
            st['unpushed'] = _records(root, f'{up}..HEAD')
        if st['behind']:
            st['incoming'] = _records(root, f'HEAD..{up}')
        common = _git(root, 'rev-parse', '--git-common-dir').strip()
        fh = os.path.join(root, common, 'FETCH_HEAD') if not os.path.isabs(common) else os.path.join(common, 'FETCH_HEAD')
        if os.path.exists(fh):
            st['fetch_age'] = _ago(time.time() - os.path.getmtime(fh))
    dflt = _git(root, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD').strip()
    st['default'] = dflt.split('/', 1)[1] if '/' in dflt else next(
        (b for b in ('main', 'master') if _git(root, 'rev-parse', '--verify', '-q', b).strip()), '')
    trees = {}
    wt, cur = _git(root, 'worktree', 'list', '--porcelain'), None
    for l in wt.splitlines():
        if l.startswith('worktree '):
            cur = l[9:]
        elif l.startswith('branch refs/heads/') and cur:
            trees[l[18:]] = cur
    for b in _git(root, 'for-each-ref', '--format=%(refname:short)%09%(committerdate:unix)',
                  'refs/heads').splitlines():
        name, _, when = b.partition('\t')
        if not name or name == st['branch']:
            continue
        n = _records(root, f'HEAD..{name}')
        if n:
            age = time.time() - int(when or 0)
            st['others'].append(dict(branch=name, records=n, tree=trees.get(name, ''),
                                     stale=age > 30 * 86400, age=_ago(age)))
    led = _conf(root, 'LEDGER_PATH')
    if led and _git(root, 'status', '--porcelain', '--', led).strip():
        st['dirty'] = 1
    return st


def share_lines(root, index_home='', this_host='', repo_id=''):
    """Human lines for the digest; empty when everything is shared."""
    st, out = share_state(root), []
    if st['unpushed']:
        out.append(f"{st['branch']} holds {st['unpushed']} ledger record{'s' if st['unpushed'] != 1 else ''} "
                   f"not pushed to {st['upstream']} ({st['ahead']} commit{'s' if st['ahead'] != 1 else ''}) — "
                   f"other machines and collaborators cannot see them yet")
    if st['incoming']:
        out.append(f"{st['upstream']} has {st['incoming']} ledger record{'s' if st['incoming'] != 1 else ''} "
                   f"not pulled here (as of the last fetch{', ' + st['fetch_age'] + ' ago' if st['fetch_age'] else ''})")
    for o in st['others'][:5]:
        where = f" (worktree {_tilde(o['tree'])})" if o['tree'] else ''
        stale = f"; last commit {o['age']} ago — abandoned?" if o['stale'] else ''
        out.append(f"branch {o['branch']}{where} holds {o['records']} ledger record"
                   f"{'s' if o['records'] != 1 else ''} not in {st['branch']}{stale}")
    if index_home and repo_id:
        for path in sorted(__import__('glob').glob(os.path.join(index_home, 'hosts', '*.tsv'))):
            host = os.path.basename(path)[:-4]
            if host == this_host:
                continue
            try:
                rows = io.open(path, encoding='utf-8').read().splitlines()
            except OSError:
                continue
            for r in rows:
                f = r.split('\t')
                if len(f) >= 10 and f[0] == repo_id and (f[6] not in ('', '0') or f[7]):
                    bits = []
                    if f[6] not in ('', '0'):
                        bits.append(f"{f[6]} ledger records not pushed from {f[2]}")
                    if f[7]:
                        bits.append('unmerged branches ' + f[7].replace(',', ', '))
                    out.append(f"on {host} (as of {f[9]}): " + '; '.join(bits))
    return out


def share_tsv(root):
    st = share_state(root)
    others = ','.join(f"{o['branch']}:{o['records']}" for o in st['others'])
    return '\t'.join(str(x) for x in (st['branch'], st['upstream'] or '-', st['ahead'], st['behind'],
                                      st['unpushed'], others, st['dirty']))


def cmd_share_state(argv):
    if len(argv) > 1 and argv[1] == '--tsv':
        return share_tsv(argv[0])
    extra = argv[1:4] if len(argv) >= 4 else ['', '', '']
    return '\n'.join(share_lines(argv[0], *extra))


# --- the Lore query side: what history says about a path -------------------------------------

LORE_QUERY = ('Directive', 'Constraint', 'Rejected', 'Not-tested', 'Tested', 'Confidence',
              'Scope-risk', 'Reversibility', 'Related')
NOT_CODE = ('.claude/', '.githooks/')


def lore_commits(root, *paths, limit=2000):
    """Commits carrying Lore trailers, newest first:
    [dict(sha, date, subject, trailers=[(key, value)], files=[...])]. With paths: only commits
    that touched them."""
    raw = _git(root, 'log', f'-n{limit}', '--no-merges', '--date=short', '--name-only',
               '--format=%x1e%h%x1f%ad%x1f%s%x1f%B%x1f', *(['--'] + list(paths) if paths else []))
    led = _conf(root, 'LEDGER_PATH')
    dec = _conf(root, 'DECISIONS_PATH')
    out = []
    for rec in raw.split('\x1e'):
        f = rec.split('\x1f')
        if len(f) < 5:
            continue
        sha, date, subj, body, tail = f[0].strip(), f[1].strip(), f[2].strip(), f[3], f[4]
        tr = [(l.split(':', 1)[0], l.split(':', 1)[1].strip()) for l in trailer_lines(body)
              if l.split(':', 1)[0] in LORE_QUERY]
        if not tr:
            continue
        files = [x for x in tail.splitlines() if x.strip() and x not in (led, dec)
                 and not x.startswith(NOT_CODE)]
        out.append(dict(sha=sha, date=date, subject=subj, trailers=tr, files=files))
    return out


def _entries_by_commit(ledger):
    m = {}
    for e in parse_entries(ledger):
        c = entry_commit(e)
        if c:
            m.setdefault(c[:7], []).append(e)
    return m


def _rel(root, path):
    ap = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    rp = os.path.relpath(ap, root)
    return path if rp.startswith('..') else rp


def context(root, ledger, path, only=None):
    """The paper's `lore context <path>`: everything history says about a file or directory,
    before an agent changes it."""
    path = _rel(root, path)
    byc = _entries_by_commit(ledger)
    groups = {k: [] for k in ('Directive', 'Constraint', 'Rejected', 'Not-tested')}
    for c in lore_commits(root, path, limit=500):
        ents = byc.get(c['sha'][:7], [])
        dead = ents and all(is_dead(e) for e in ents)
        ids = ', '.join(e['id'] for e in ents) if len(ents) <= 3 else f"{len(ents)} entries"
        tag = f"{c['sha']} {c['date']}" + (f" · {ids}" if ents else '')
        if dead:
            tag += ' · its entry is superseded/closed — may no longer hold'
        for k, v in c['trailers']:
            if k in groups and (only is None or k == only):
                groups[k].append(f"- {v}  ({tag})")
    out = [f"# What the ledger and history say about {path}", ""]
    titles = {'Directive': 'Directives — instructions to whoever changes this next',
              'Constraint': 'Constraints — rules that shaped it and may still hold',
              'Rejected': 'Rejected alternatives — do not re-propose without new evidence',
              'Not-tested': 'Known untested'}
    for k, t in titles.items():
        if groups[k]:
            out += [f"## {t}", *groups[k], ""]
    if only is None:
        shas = {x[:7] for x in _git(root, 'log', '-n1000', '--format=%h', '--', path).split()}
        ents = [e for e in parse_entries(ledger) if entry_commit(e)[:7] in shas]
        if ents:
            out.append("## Ledger entries made by commits that touched it")
            out += [f"- {e['id']} · {e['status']} · {entry_text(e)[:150]}" for e in ents[:30]]
            out.append("")
    if len(out) == 2:
        out.append("Nothing recorded for this path.")
    return "\n".join(out).rstrip()


def stale_directives(root, ledger, threshold=10, older_than_days=0):
    """The paper's `lore stale`: directives and constraints whose code has changed a lot since
    they were written — the ones most likely to be quietly untrue now."""
    today = datetime.date.today()
    byc = _entries_by_commit(ledger)
    out = []
    for c in lore_commits(root, limit=2000):
        if not c['files'] or not any(k in ('Directive', 'Constraint') for k, _ in c['trailers']):
            continue
        ents = byc.get(c['sha'][:7], [])
        if ents and all(is_dead(e) for e in ents):
            continue
        age = (today - datetime.date.fromisoformat(c['date'])).days
        if age < older_than_days:
            continue
        n = int(_git(root, 'rev-list', '--count', f"{c['sha']}..HEAD", '--', *c['files'][:20]).strip() or 0)
        if n >= threshold:
            for k, v in c['trailers']:
                if k in ('Directive', 'Constraint'):
                    out.append(f"- {k}: {v[:120]}  ({c['sha']} {c['date']}; its files changed {n} "
                               f"times since — still true?)")
    return out


RULES_MARK = '<!-- generated by living-ledger: regenerated every session; edit history, not this file -->'


def write_rules(root, ledger, outdir):
    """Path-scoped Claude Code rules generated from Directive/Constraint/Rejected trailers: each
    loads when Claude reads a file the commit touched — the paper's constraint harvest, delivered
    before the change instead of fetched on request. The directory is regenerated in full."""
    byc = _entries_by_commit(ledger)
    os.makedirs(outdir, exist_ok=True)
    keep = set()
    for c in lore_commits(root, limit=2000):
        lines = [(k, v) for k, v in c['trailers'] if k in ('Directive', 'Constraint', 'Rejected')]
        if not lines or not c['files']:
            continue                      # a record with no files is the digest's, not a rule
        ents = byc.get(c['sha'][:7], [])
        if ents and all(is_dead(e) for e in ents):
            continue
        files = c['files']
        if len(files) > 20:
            common = os.path.commonpath(files) if all('/' in f for f in files) else ''
            if not common or '.' in os.path.basename(common):
                continue                  # a sweeping commit: too broad to scope a rule to
            globs = [common.rstrip('/') + '/**']
        else:
            globs = files
        name = f"{c['date']}-{c['sha'][:7]}.md"
        keep.add(name)
        ids = (f" · {', '.join(e['id'] for e in ents)}" if len(ents) <= 3
               else f" · {len(ents)} ledger entries") if ents else ''
        body = ['---', 'paths:'] + [f'  - "{g.replace(chr(34), "")}"' for g in globs] + ['---', RULES_MARK,
                f"# From the ledger — {c['sha']} ({c['date']}){ids}", f"_{c['subject']}_", '']
        body += [f"- **{k}:** {v}" for k, v in lines]
        text = '\n'.join(body) + '\n'
        path = os.path.join(outdir, name)
        try:
            old = io.open(path, encoding='utf-8').read()
        except OSError:
            old = ''
        if old != text:
            io.open(path, 'w', encoding='utf-8').write(text)
    for f in os.listdir(outdir):
        if f.endswith('.md') and f not in keep:
            os.remove(os.path.join(outdir, f))
    return len(keep)


def validate(root, n=20):
    """The paper's `lore validate`: the gate's structural rules over the last n commits, for
    history made without the hooks (another machine, a web edit, an old template)."""
    out, bad = [], 0
    raw = _git(root, 'log', f'-n{n}', '--no-merges', '--format=%h%x1f%an <%ae>%x1f%B%x1e')
    for rec in raw.split('\x1e'):
        f = rec.strip('\n').split('\x1f')
        if len(f) < 3:
            continue
        probs = check_body(f[2].strip(), root, f[1], structural_only=True)
        if probs:
            bad += 1
            subj = f[2].strip().splitlines()[0][:70]
            out.append(f"- {f[0]} {subj}\n    " + "\n    ".join(p.splitlines()[0][:160] for p in probs))
    out.insert(0, f"{bad} of the last {n} commits do not relate to the ledger as the gate requires"
                  + (":" if bad else "."))
    return "\n".join(out)


CLI_HELP = """ledger — query this repo's ledger and the decision history in its commits.

  ledger context <path>       everything recorded about a file or directory: directives,
                              constraints, rejected alternatives, untested areas, entries
  ledger directives <path>    …only the directives   (also: constraints, rejected)
  ledger search <words>       entries about a topic — live, closed, superseded or retired —
                              ranked by relevance (an id in the words ranks first)
  ledger open                 open problems, questions and actions (overdue first)
  ledger decisions            decisions in force, newest first
  ledger retired              approaches that are dead — never re-propose these
  ledger stale [N]            directives/constraints whose files changed >= N times since (10)
  ledger validate [N]         check the last N commits against the ledger's commit rules (20)
  ledger rules                regenerate .claude/rules/ledger/ from directives/constraints/rejected
  ledger tidy                 the tidy report      ledger share   records not shared yet

Works for any agent or person that can run a shell command. Writing happens only through
commit trailers (Decision:, Finding:, Opens:, … — see the ledger's own header)."""


def cmd_cli(argv):
    root, cmd, rest = argv[0], (argv[1] if len(argv) > 1 else 'help'), argv[2:]
    ledger = _ledger_path(root)
    if cmd in ('help', '-h', '--help'):
        return CLI_HELP
    if not ledger or not os.path.exists(ledger):
        return "No ledger in this repo (run /ledger-init, or install.sh <repo>)."
    if cmd in ('context', 'directives', 'constraints', 'rejected'):
        if not rest:
            return f"usage: ledger {cmd} <path>"
        only = {'directives': 'Directive', 'constraints': 'Constraint', 'rejected': 'Rejected'}.get(cmd)
        return context(root, ledger, rest[0], only)
    entries = parse_entries(ledger)
    if cmd == 'search':
        if not rest:
            return "usage: ledger search <words>"
        hits = search(entries, ' '.join(rest), k=12)
        return "\n".join(f"- {_status_line(e)} — {entry_text(e)[:180]}  [{sc}]" for sc, e, _ in hits) \
            or "Nothing in the ledger matches."
    if cmd == 'open':
        today = datetime.date.today().isoformat()
        op = [e for e in entries if e['status'] == 'OPEN']
        op = sorted((e for e in op if entry_due(e) and entry_due(e) <= today), key=entry_due) + \
             [e for e in op if not (entry_due(e) and entry_due(e) <= today)]
        return "\n".join(_one(e, width=240) for e in op) or "Nothing open."
    if cmd == 'decisions':
        return "\n".join(_one(e, width=240, dated=True) for e in entries
                         if e['type'] == 'decision' and not is_dead(e)) or "No decisions in force."
    if cmd == 'retired':
        return "\n".join(_one(e, width=240) for e in entries
                         if e['type'] == 'retired' and not is_dead(e)) or "Nothing retired."
    if cmd == 'stale':
        rows = stale_directives(root, ledger, int(rest[0]) if rest else 10)
        return "\n".join(rows) or "No directive or constraint has seen that much change since."
    if cmd == 'validate':
        return validate(root, int(rest[0]) if rest else 20)
    if cmd == 'rules':
        n = write_rules(root, ledger, os.path.join(root, '.claude', 'rules', 'ledger'))
        return f"{n} path-scoped rule file{'s' if n != 1 else ''} in .claude/rules/ledger/"
    if cmd == 'tidy':
        return tidy_report(ledger, root)
    if cmd == 'share':
        return "\n".join(share_lines(root)) or "Everything is shared."
    return f"unknown command: {cmd}\n\n{CLI_HELP}"


# --- search: the ledger by topic (BM25 over every entry, live or dead) -------------------------

SEARCH_STOP = STOP | set(
    'about above after again also any because been before being both but can could did does '
    'doing done down during few from further had has have having here how its just like make '
    'made more most much need not now off once other our out over own same should some still '
    'such than that their them then there these they this those through too under until very '
    'was were what when where which while who why will with would you your yes let lets want '
    'think know see look going get got use used using ledger entry entries commit commits'.split())


def _stem(w):
    if len(w) > 5 and w.endswith('ing'):
        return w[:-3]
    if len(w) > 4 and w.endswith('ies'):
        return w[:-3] + 'y'
    if len(w) > 4 and w.endswith('ed'):
        return w[:-2]
    if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
        return w[:-1]
    return w


def _terms(text):
    return [_stem(w) for w in re.findall(r'[a-z0-9_]+', text.lower())
            if len(w) >= 3 and w not in SEARCH_STOP and not w.isdigit()]


def _doc(e):
    """What an entry is searchable by: its statement, its modifier/Lore lines, what replaced it."""
    return ' '.join([entry_text(e)] + [l for l in e['lines'] if l[:1] in ('·', '⤳')])


def search(entries, query, k=10, exclude=(), min_terms=1):
    """BM25 ranking of entries for a free-text query. An id named in the query ranks first.
    -> [(score, entry, matched_terms)]"""
    import math
    docs = [(e, _terms(_doc(e))) for e in entries]
    n = len(docs) or 1
    avg = sum(len(t) for _, t in docs) / n or 1.0
    df = {}
    for _, t in docs:
        for w in set(t):
            df[w] = df.get(w, 0) + 1
    q = list(dict.fromkeys(_terms(query)))
    named = set(ID_RE.findall(query))
    out = []
    for e, t in docs:
        if e['id'] in exclude:
            continue
        tf = {}
        for w in t:
            tf[w] = tf.get(w, 0) + 1
        matched = [w for w in q if w in tf]
        score = 0.0
        for w in matched:
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            f = tf[w]
            score += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(t) / avg))
        if e['id'] in named:
            score += 100.0
        elif len(matched) < min_terms:
            continue
        if score > 0:
            out.append((round(score, 2), e, matched))
    out.sort(key=lambda x: -x[0])
    return out[:k]


def _status_line(e):
    extra = ''
    if e['status'] == 'SUPERSEDED':
        by = next((l for l in e['lines'] if l.startswith('⤳')), '')
        m = re.search(r'superseded by ([^ ]+(?:, [^ ]+)*) in', by)
        extra = f" by {m.group(1)}" if m else ''
    return f"{e['id']} · {e['status']}{extra} · {e['type']} · {e['date']}"


def cmd_id(argv):
    return hash_id(argv[0], ' '.join(argv[1:]))


def main():
    if len(sys.argv) < 2:
        sys.exit(2)
    mode, rest = sys.argv[1], sys.argv[2:]
    fn = {'digest': cmd_digest, 'block': cmd_block, 'stale-rules': cmd_stale_rules,
          'lint': cmd_lint, 'id': cmd_id, 'check-msg': cmd_check_msg,
          'tidy-status': cmd_tidy_status, 'tidy-report': cmd_tidy_report,
          'share-state': cmd_share_state, 'cli': cmd_cli}.get(mode)
    if fn is None:
        sys.exit(2)
    s = fn(rest)
    if s:
        print(s)


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    main()
