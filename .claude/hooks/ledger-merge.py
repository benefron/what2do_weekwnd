#!/usr/bin/env python3
"""living-ledger — git merge driver for LEDGER.md, DECISIONS.md and the dashboard blocks.

    ledger-merge.py <base> <ours> <theirs> [<path>]          # merge=ledger
    ledger-merge.py --block <base> <ours> <theirs> [<path>]  # merge=ledger-block (index repo)

Two branches (or two machines, or a cloud session's PR) both insert entries directly under
<!-- ENTRIES_START -->: for git that is a guaranteed textual conflict. Entries are independent
blocks keyed by id, so they are merged entry-wise:

  * union by id; an entry new on either side is kept (the other side's new entries go on top,
    so the file stays newest-first);
  * the same id changed on both sides: the header takes the more final status
    (OPEN < STANDING < CLOSED < SUPERSEDED) and the body is the union of both sides' lines, so
    a backlink or a closing line added on each branch both survive;
  * a TRUE COLLISION — the same legacy sequential id (D-013) holding different text from a
    different commit — is two entries, not a conflict: ours keeps the id, theirs is re-keyed to
    its content-hash id with a `· was D-013` note. Nothing is dropped;
  * the same entry under a legacy id on one side and a hash id on the other collapses to one,
    keeping the legacy id (commit messages may already cite it);
  * the prose above the marker is merged 3-way with `git merge-file`; only a real overlapping
    edit leaves conflict markers (exit 1, git flags the file).

DECISIONS.md: the prose is merged 3-way the same way; the auto log between
<!-- DECISIONS_LOG_START/END --> is the union of both sides' rows, dated order.

--block: a dashboard block / DASHBOARD.md is regenerated, never hand-edited: whole-file, the
newest `_rebuilt <UTC stamp>` wins.

Result is written to <ours> (git's contract). Exit 0 = merged, 1 = conflict left for a human.

ledger-template-version: 4
"""
import io
import os
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ledger_parse import (ENTRIES_MARKER, HDR, hash_id, is_legacy, norm_text,  # noqa: E402
                           parse_blocks, split_ledger, entry_text, entry_commit)

STATUS_RANK = {'OPEN': 0, 'STANDING': 1, 'CLOSED': 2, 'SUPERSEDED': 3}
LOG_START, LOG_END = '<!-- DECISIONS_LOG_START -->', '<!-- DECISIONS_LOG_END -->'
STAMP = re.compile(r'^_rebuilt (\S+)', re.M)


def read(p):
    try:
        return io.open(p, encoding='utf-8').read()
    except OSError:
        return ''


def merge3(base, ours, theirs):
    """3-way text merge via `git merge-file`. -> (text, conflicted)."""
    if ours == theirs or theirs == base:
        return ours, False
    if ours == base:
        return theirs, False
    d = tempfile.mkdtemp()
    paths = []
    for name, content in (('ours', ours), ('base', base), ('theirs', theirs)):
        p = os.path.join(d, name)
        io.open(p, 'w', encoding='utf-8').write(content)
        paths.append(p)
    r = subprocess.run(['git', 'merge-file', '-p', '-L', 'ours', '-L', 'base', '-L', 'theirs']
                       + paths, capture_output=True, text=True)
    for p in paths:
        os.unlink(p)
    os.rmdir(d)
    if r.returncode < 0 or (r.returncode > 0 and '<<<<<<<' not in r.stdout):
        return f'<<<<<<< ours\n{ours}\n=======\n{theirs}\n>>>>>>> theirs\n', True
    return r.stdout, r.returncode > 0


def rank(s):
    return STATUS_RANK.get(s, 0)


def set_status(raw, status):
    hdr, sep, rest = raw.partition('\n')
    hdr = re.sub(r'^(## \S+ · )\S+', lambda m: m.group(1) + status, hdr)
    return hdr + sep + rest


def union_body(oe, te):
    """Ours' block, plus any body line only theirs has; header status = the more final one."""
    olines = oe['raw'].split('\n')
    have = {l.strip() for l in olines[1:]}
    extra = [l for l in te['raw'].split('\n')[1:] if l.strip() and l.strip() not in have]
    raw = '\n'.join(olines + extra)
    best = max((oe['status'], te['status']), key=rank)
    return set_status(raw, best)


def same_origin(a, b):
    """Same entry, edited apart — as opposed to one legacy id allocated to two entries."""
    ca, cb = entry_commit(a), entry_commit(b)
    return bool(ca and ca == cb) or norm_text(entry_text(a)) == norm_text(entry_text(b))


def rekey(e):
    """Re-key a colliding legacy entry to its content-hash id, keeping a note of the old id."""
    new_id = hash_id(e['id'].split('-')[0], entry_text(e))
    lines = e['raw'].split('\n')
    lines[0] = lines[0].replace(f'## {e["id"]} ', f'## {new_id} ', 1)
    lines.insert(2 if len(lines) > 1 else 1,
                 f'· was {e["id"]} — renamed on merge (the id was allocated twice on different clones)')
    return new_id, '\n'.join(lines)


def merge_entries(base, ours, theirs):
    def index(text):
        blocks = parse_blocks(text)
        lead = next((b['raw'] for b in blocks if b['id'] is None), '')
        return lead, {b['id']: b for b in blocks if b['id']}, [b['id'] for b in blocks if b['id']]

    bl, b, _ = index(base)
    ol, o, oorder = index(ours)
    tl, t, torder = index(theirs)
    lead, lead_conflict = merge3(bl, ol, tl)

    out, top, renamed = {}, [], []
    for eid in oorder:
        oe, te, be = o[eid], t.get(eid), b.get(eid)
        if te is None:
            if be is not None and be['raw'] == oe['raw']:
                continue                  # theirs deleted it and ours did not touch it
            out[eid] = oe['raw']
        elif oe['raw'] == te['raw']:
            out[eid] = oe['raw']
        elif be is not None and be['raw'] == oe['raw']:
            out[eid] = te['raw']          # changed on theirs only
        elif be is not None and be['raw'] == te['raw']:
            out[eid] = oe['raw']          # changed on ours only
        elif is_legacy(eid) and not same_origin(oe, te):
            out[eid] = oe['raw']
            new_id, block = rekey(te)
            renamed.append((eid, new_id))
            top.append((new_id, block))
        else:
            out[eid] = union_body(oe, te)
    for eid in torder:
        if eid in o:
            continue
        te, be = t[eid], b.get(eid)
        if be is not None:
            if be['raw'] == te['raw']:
                continue                  # ours deleted it and theirs did not touch it
            out[eid] = te['raw']          # ours deleted, theirs changed: keep theirs
            continue
        top.append((eid, te['raw']))      # new on theirs

    # the other side's new entries go in by date — above the first entry dated on or before
    # them — so the file stays newest-first; existing order is never rewritten
    merged = list(out.items())

    def date_of(raw):
        m = HDR.match(raw.split('\n', 1)[0])
        return m.group(5) if m else '9999-99-99'
    for i, r in reversed([(i, r) for i, r in top if i not in out]):
        d = date_of(r)
        pos = next((k for k, (_, rr) in enumerate(merged) if date_of(rr) <= d), len(merged))
        merged.insert(pos, (i, r))
    # One entry under two ids (a legacy counter id on one clone, a hash id derived from git on
    # the other): keep the legacy id, lift its status to the more final of the two.
    final, seen = [], {}
    for eid, raw in merged:
        es = parse_blocks(raw)
        e = es[0] if es and es[0]['id'] else None
        key = (entry_commit(e), norm_text(entry_text(e))[:60]) if e and entry_commit(e) else None
        if key is None or key not in seen:
            if key is not None:
                seen[key] = len(final)
            final.append([eid, raw])
            continue
        j = seen[key]
        other_id, other_raw = final[j]
        keep_other = is_legacy(other_id) or not is_legacy(eid)
        k_raw, d_raw = (other_raw, raw) if keep_other else (raw, other_raw)
        k_id = other_id if keep_other else eid
        ke, de = parse_blocks(k_raw)[0], parse_blocks(d_raw)[0]
        final[j] = [k_id, set_status(k_raw, max((ke['status'], de['status']), key=rank))]
    body = '\n\n'.join(r for _, r in final)
    if lead:
        body = lead.rstrip() + ('\n\n' + body if body else '')
    return body, renamed, lead_conflict


def merge_ledger(base, ours, theirs):
    bp, be = split_ledger(base)
    op, oe = split_ledger(ours)
    tp, te = split_ledger(theirs)
    pre, conflict = merge3(bp, op, tp)
    if ENTRIES_MARKER not in pre:       # conflict markers swallowed it; keep the file parseable
        pre = pre.rstrip('\n') + '\n' + ENTRIES_MARKER
    body, renamed, lead_conflict = merge_entries(be, oe, te)
    for old, new in renamed:
        sys.stderr.write(f'ledger-merge: id {old} was allocated twice; theirs re-keyed to {new}\n')
    return pre.rstrip('\n') + '\n\n' + body.rstrip() + '\n', conflict or lead_conflict


def split_log(text):
    if LOG_START in text and LOG_END in text:
        head, _, rest = text.partition(LOG_START)
        rows, _, tail = rest.partition(LOG_END)
        return head, rows, tail
    return None


def merge_decisions(base, ours, theirs):
    parts = [split_log(x) for x in (base, ours, theirs)]
    if not all(parts[1:]):
        return merge3(base, ours, theirs)
    b = parts[0] or ('', '', '')
    (oh, orows, ot), (th, trows, tt) = parts[1], parts[2]
    head, c1 = merge3(b[0], oh, th)
    tail, c2 = merge3(b[2], ot, tt)
    rows, seen = [], set()
    for line in orows.splitlines() + trows.splitlines():
        if not line.strip():
            continue
        m = re.match(r'^\|?\s*(\d{4}-\d{2}-\d{2}[^|·]*?)\s*[|·]\s*(\S+)\s*[|·]', line)
        key = m.group(2) if m else line.strip()
        if key in seen:
            continue
        seen.add(key)
        rows.append((m.group(1) if m else '', len(rows), line))
    rows.sort(key=lambda r: (r[0], r[1]))
    body = '\n' + '\n'.join(r[2] for r in rows) + ('\n' if rows else '')
    return head + LOG_START + body + LOG_END + tail, c1 or c2


def merge_block(base, ours, theirs):
    om, tm = STAMP.search(ours), STAMP.search(theirs)
    if tm and (not om or tm.group(1) > om.group(1)):
        return theirs, False
    return ours, False


def main(argv):
    block = bool(argv) and argv[0] == '--block'
    if block:
        argv = argv[1:]
    if len(argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    base, ours, theirs = (read(p) for p in argv[:3])
    if block:
        text, conflict = merge_block(base, ours, theirs)
    elif ENTRIES_MARKER in ours or ENTRIES_MARKER in theirs:
        text, conflict = merge_ledger(base, ours, theirs)
    elif LOG_START in ours or LOG_START in theirs:
        text, conflict = merge_decisions(base, ours, theirs)
    else:
        text, conflict = merge3(base, ours, theirs)
    io.open(argv[1], 'w', encoding='utf-8').write(text)
    return 1 if conflict else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
