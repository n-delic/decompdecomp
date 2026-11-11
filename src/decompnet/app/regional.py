"""Another release of the same game (Europe, North America, Japan): found, paired and given its names.

Two releases are mostly the same code at shifted addresses. A Europe N64 rally game, matched and polished, and
its North America release, half done, shared 1,013 byte-identical functions; their relocations agreed on how
1,792 addresses moved from one release to the other, with one exception. Yet twins matched 11 functions there:
the donor's C names its own release's addresses, and the per-function guess at the other release's names
(twins.rom_renames) is fragile.

So once the user links the two projects, this module:
1. pairs their functions: identical code first, then the rest by their order in the game (`pair`);
2. learns how addresses move, from the relocations of the identical pairs (`AddressMap`);
3. gives this project the other release's names and missing header types (`bring_names`), through the
   checked bulk rename (symbol_names) and header edit (edit_context): a name or type is kept only if every
   matched function still matches.

Detection: a console's `release_key` (N64 and GBA game codes end in the region's letter), else enough identical
code between two games of the same console (`shared_fraction`). ROM mode only: a project-mode game's functions
come from its object files, which need their own pairing (ROADMAP.md).
"""
from __future__ import annotations

import bisect
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..network import IN_BUILD, NetworkError
from ..rom.symbols import AUTO_NAME
from .function_names import generated as _generated

SHARED_ENOUGH = 0.4  # without a release key: share of the smaller game's functions that are identical
NEAR = 0.8  # instruction similarity for two functions in the same place to be paired
MAX_GAP_CELLS = 40000  # a gap between identical pairs larger than this (functions x functions) stays unpaired
MAX_SPREAD = 0x10000  # an address between two learned ones moves with them only this close
CHUNK = 256  # names renamed together; a rejected chunk is split to find the name that breaks a match
MIN_BYTES = 32  # as twins.ACROSS_MIN_BYTES: tiny functions are identical across unrelated games




def generated(name: str) -> bool:
    """A name made from an address (func_, D_, jtbl_...), not one someone gave."""
    return _generated(name) or bool(AUTO_NAME.fullmatch(name))


# ------------------------------------------------------------------ detection

def release_key(net, proj) -> str | None:
    return net.console(proj).release_key(net.cfg(proj).get('rom') or {})


def _eligible(net, proj) -> bool:
    return net.is_rom(proj) and not net.fork_slug(proj['slug']) and proj['slug'] not in net.links


def _prints(net, proj):
    return {fid: p for fid, p in net.twins.prints(proj).items() if p.size >= MIN_BYTES and not p.asm}


def shared_fraction(net, proj, other) -> float:
    """Share of the smaller game's functions whose code is identical in the other (addresses masked)."""
    mine, theirs = _prints(net, proj), _prints(net, other)
    if not mine or not theirs:
        return 0.0
    a, b = Counter(p.key for p in mine.values()), Counter(p.key for p in theirs.values())
    return sum((a & b).values()) / min(len(mine), len(theirs))


def _summary(net, proj) -> dict:
    rom = net.cfg(proj).get('rom') or {}
    counts = {r['status']: r['n'] for r in net.project_store(proj['id']).all(
        f"SELECT status, COUNT(*) AS n FROM functions WHERE project_id=? AND {IN_BUILD} GROUP BY status", proj['id'])}
    return dict(slug=proj['slug'], name=proj['name'] or proj['slug'], title=rom.get('title', ''),
                region=rom.get('region', ''), game_code=rom.get('game_code', ''),
                matched=counts.get('matched', 0), functions=sum(counts.values()))


def candidates(net, proj, *, shared=False) -> list[dict]:
    """The user's other projects that look like another release of this game, best evidence first. `shared`:
    also compare code with every ROM project of the same console (slower; release keys are instant)."""
    if not _eligible(net, proj):
        return []
    from .shared_reuse import compatible
    cfg = net.cfg(proj)
    rom, key = cfg.get('rom') or {}, release_key(net, proj)
    out = []
    for other in compatible(net, proj):
        if not _eligible(net, other):
            continue
        ocfg = net.cfg(other)
        orom = ocfg.get('rom') or {}
        if ocfg.get('platform') != cfg.get('platform') or (rom.get('sha1') and orom.get('sha1') == rom.get('sha1')):
            continue  # the same file is the same release: a room shares that work
        if key and key == release_key(net, other):
            out.append(_summary(net, other) | dict(evidence='game code', shared=None))
        elif shared:
            fraction = shared_fraction(net, proj, other)
            if fraction >= SHARED_ENOUGH:
                out.append(_summary(net, other) | dict(evidence='identical code', shared=round(fraction, 3)))
    return sorted(out, key=lambda c: (c['evidence'] != 'game code', -c['matched']))


def linked(net, proj) -> dict | None:
    """The release this project takes names from, while that project still exists."""
    link = net.cfg(proj).get('release_of')
    if not link:
        return None
    try:
        other = net.project_row(link['slug'])
    except (NetworkError, KeyError, ValueError):
        return None
    return dict(link, **_summary(net, other))


def link(net, slug: str, other: str | None) -> dict:
    """Record (or clear, with no `other`) the release this project takes names and code from."""
    proj = net.project_row(slug)
    if not _eligible(net, proj):
        raise NetworkError('Releases can be linked between local ROM projects only.')
    if other:
        donor = net.project_row(other)
        if donor['id'] == proj['id'] or not _eligible(net, donor) or \
                net.cfg(donor).get('platform') != net.cfg(proj).get('platform'):
            raise NetworkError('Choose another ROM project of the same console as the other release.')
        value = dict(slug=donor['slug'], id=donor['id'], linked_at=time.time())
        net._update_cfg(slug, lambda cfg: cfg.__setitem__('release_of', value))
    else:
        net._update_cfg(slug, lambda cfg: cfg.pop('release_of', None))
    return status(net, slug)


def status(net, slug: str, *, shared=False) -> dict:
    proj = net.project_row(slug)
    if not _eligible(net, proj):
        return dict(supported=False, linked=None, candidates=[])
    current = linked(net, proj)
    return dict(supported=True, linked=current,
                candidates=[c for c in candidates(net, proj, shared=shared) if not current or c['slug'] != current['slug']])


# ------------------------------------------------------------------ pairing

@dataclass
class Fn:
    id: int
    name: str
    rom: int
    vram: int
    key: str
    size: int
    status: str = 'open'
    relocs: list = field(default_factory=list)  # (offset, type, address)
    ops: tuple = ()


def pair(a: list[Fn], b: list[Fn], similar) -> list[tuple[Fn, Fn, float]]:
    """Functions of release `a` paired with release `b`'s: identical code (a key once on each side, or as often
    on both, in order) kept where it agrees with the game's order; then, between two such pairs, functions in
    the same place whose instructions are at least NEAR alike. `similar(x, y)` -> 0..1. Returns (a, b, 1.0 for
    identical code or the similarity)."""
    a, b = sorted(a, key=lambda f: f.rom), sorted(b, key=lambda f: f.rom)
    at_a, at_b = {f.id: i for i, f in enumerate(a)}, {f.id: i for i, f in enumerate(b)}
    by_a, by_b = defaultdict(list), defaultdict(list)
    for f in a:
        by_a[f.key].append(f)
    for f in b:
        by_b[f.key].append(f)
    exact = sorted(((at_a[x.id], at_b[y.id]) for key, xs in by_a.items() if len(xs) == len(by_b.get(key, ()))
                    for x, y in zip(xs, by_b[key])))
    anchors = _increasing(exact)
    out = [(a[i], b[j], 1.0) for i, j in anchors]
    bounds = [(-1, -1)] + anchors + [(len(a), len(b))]
    for (i0, j0), (i1, j1) in zip(bounds, bounds[1:]):
        out += _align(a[i0 + 1:i1], b[j0 + 1:j1], similar)
    return out


def _increasing(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The longest chain of (i, j) with both increasing: identical pairs that agree with the game's order."""
    tails, links, back = [], [], {}
    for p in points:
        k = bisect.bisect_left(tails, p[1])
        if k == len(tails):
            tails.append(p[1]); links.append(p)
        else:
            tails[k], links[k] = p[1], p
        back[p] = links[k - 1] if k else None
    chain, p = [], links[-1] if links else None
    while p is not None:
        chain.append(p); p = back[p]
    return chain[::-1]


def _align(xs: list[Fn], ys: list[Fn], similar) -> list[tuple[Fn, Fn, float]]:
    """In order, the pairs of a gap that are NEAR alike, maximizing their total similarity."""
    if not xs or not ys or len(xs) * len(ys) > MAX_GAP_CELLS:
        return []
    n, m = len(xs), len(ys)
    sim = [[0.0] * m for _ in range(n)]
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            if min(x.size, y.size) >= 0.7 * max(x.size, y.size):
                s = similar(x, y)
                sim[i][j] = s if s >= NEAR else 0.0
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best[i][j] = max(best[i + 1][j], best[i][j + 1], sim[i][j] + best[i + 1][j + 1] if sim[i][j] else 0.0)
    out, i, j = [], 0, 0
    while i < n and j < m:
        if sim[i][j] and best[i][j] == sim[i][j] + best[i + 1][j + 1]:
            out.append((xs[i], ys[j], sim[i][j])); i += 1; j += 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return out


def similarity(x: Fn, y: Fn) -> float:
    if not x.ops or not y.ops:
        return 0.0
    matcher = SequenceMatcher(None, x.ops, y.ops, autojunk=False)
    return matcher.ratio() if matcher.real_quick_ratio() >= NEAR and matcher.quick_ratio() >= NEAR else 0.0


# ------------------------------------------------------------------ how addresses move

class AddressMap:
    """Release A's addresses in release B, learned from identical pairs: each relocation fills in an address in
    both, and paired functions start at each other's address. An address voted two ways is dropped; one between
    two learned addresses that moved by the same amount, within MAX_SPREAD, moves with them."""

    def __init__(self, pairs: list[tuple[Fn, Fn, float]]):
        votes = defaultdict(Counter)
        for x, y, score in pairs:
            votes[x.vram][y.vram] += 1
            if score == 1.0 and [(o, t) for o, t, _ in x.relocs] == [(o, t) for o, t, _ in y.relocs]:
                for (_, _, ax), (_, _, ay) in zip(x.relocs, y.relocs):
                    votes[ax][ay] += 1
        self.exact = {a: next(iter(c)) for a, c in votes.items() if len(c) == 1}
        self.conflicts = len(votes) - len(self.exact)
        self.known = sorted(self.exact)

    def __len__(self):
        return len(self.exact)

    def get(self, address: int) -> int | None:
        if address in self.exact:
            return self.exact[address]
        i = bisect.bisect_right(self.known, address)
        if i == 0 or i == len(self.known):
            return None
        lo, hi = self.known[i - 1], self.known[i]
        delta = self.exact[lo] - lo
        if self.exact[hi] - hi != delta or hi - lo > MAX_SPREAD:
            return None
        return address + delta


def functions(net, proj) -> list[Fn]:
    from .project_functions import _ops
    prints = net.twins.prints(proj)
    cpu = getattr(net.console(proj), 'cpu_variant', None)
    out = []
    for r in net.project_store(proj['id']).all(
            f"SELECT id,name,status,vram,rom_offset,target,relocs FROM functions WHERE project_id=? AND {IN_BUILD} "
            "AND rom_offset IS NOT NULL AND target IS NOT NULL", proj['id']):
        p = prints.get(r['id'])
        if p is None or p.asm:
            continue
        relocs = sorted((int(o), int(t), int(a)) for o, t, a, *_ in json.loads(r['relocs'] or '[]'))
        out.append(Fn(r['id'], r['name'], r['rom_offset'], r['vram'], p.key, p.size, r['status'], relocs,
                      _ops(bytes(r['target']), proj['arch'], bool(proj['big_endian']), cpu)))
    return out


@dataclass
class Plan:
    pairs: list
    addresses: AddressMap
    names: list[dict]  # rows for symbol_names: old, new, address (in this project)
    kept: int  # this project's own names, kept over the other release's
    unmapped: int  # the other release's names at addresses that didn't map
    taken: int  # names this project already uses elsewhere
    translate: dict[str, str]  # the other release's names -> this project's now, for its C and header
    name_at: object = None  # this project's name at an address: (name, offset)

    def name(self, word: str) -> str | None:
        """This project's name for one of the other release's: given, or an address name (D_8009B5D0) moved as
        the address map says. None: no name here (unmapped, or inside one of this project's own symbols)."""
        if word in self.translate:
            return self.translate[word]
        m = AUTO_NAME.fullmatch(word)
        if not m or self.name_at is None:
            return None
        address = self.addresses.get(int(m.group(1), 16))
        if address is None:
            return None
        name, offset = self.name_at(address)
        return None if offset else name

    def text(self, source: str) -> str:
        """The other release's C or header text with this project's names."""
        return _IDENT.sub(lambda m: self.name(m.group(0)) or m.group(0), source)


def plan(net, proj, donor) -> Plan:
    """What `donor` (the other release) can give `proj`: pairs, the address map and the names."""
    mine, theirs = functions(net, proj), functions(net, donor)
    pairs = pair(theirs, mine, similarity)
    addresses = AddressMap(pairs)
    syms, donor_syms = net.symbols(proj['id']), net.symbols(donor['id'])
    rows, kept, unmapped, taken, translate, seen = [], 0, 0, 0, {}, set()

    def offer(old: str, new: str, address: int):
        nonlocal kept, taken
        translate[new] = old  # what this project calls it now (the same name once it's brought over)
        if old == new or new in seen:
            return
        if not generated(old):
            kept += 1
            return
        at = syms.resolve(new)
        if at is not None and at != address:
            taken += 1
            return
        seen.add(new)
        rows.append(dict(old=old, new=new, address=address))

    for x, y, _ in pairs:
        if generated(x.name):
            translate[x.name] = y.name
        else:
            offer(y.name, x.name, y.vram)
    functions_at = {x.vram for x, _, _ in pairs}
    for s in donor_syms.by_name.values():
        if s.address in functions_at or s.kind in ('func', 'function'):
            continue
        address = addresses.get(s.address)
        if address is None:
            unmapped += 0 if generated(s.name) else 1
            continue
        current, offset = syms.name_at(address)
        if offset:
            continue  # inside one of this project's own symbols: its layout differs
        if generated(s.name):
            translate[s.name] = current
        else:
            offer(current, s.name, address)
    return Plan(pairs, addresses, rows, kept, unmapped, taken, translate, syms.name_at)


# ------------------------------------------------------------------ header types

_COMMENT = re.compile(r'/\*.*?\*/|//[^\n]*', re.S)
_IDENT = re.compile(r'[A-Za-z_]\w*')
_KEYWORDS = frozenset('auto break case char const continue default do double else enum extern float for goto if '
                      'inline int long register return short signed sizeof static struct switch typedef union '
                      'unsigned void volatile while __attribute__ __inline__'.split())


def header_items(text: str) -> list[str]:
    """A header's top-level items: preprocessor lines, and declarations ending in a `;` outside braces."""
    text = _COMMENT.sub(' ', text)
    items, current, depth, lines = [], [], 0, text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        if depth == 0 and not ''.join(current).strip() and line.lstrip().startswith('#'):
            block = [line]
            while block[-1].rstrip().endswith('\\') and i + 1 < len(lines):
                i += 1; block.append(lines[i])
            items.append('\n'.join(block).strip())
        else:
            for ch in line + '\n':
                current.append(ch)
                depth += (ch in '{(') - (ch in '})')
                if ch == ';' and depth == 0:
                    item = ''.join(current).strip()
                    if item != ';':
                        items.append(item)
                    current = []
        i += 1
    return items


def defines(item: str) -> tuple[str, set[str]]:
    """('type' or 'decl', the names an item defines): a macro, typedef or tagged type; or what it declares."""
    if item.startswith('#'):
        m = re.match(r'#\s*define\s+([A-Za-z_]\w*)', item)
        return 'type', {m.group(1)} if m else set()
    names = {f'{kind} {tag}' for kind, tag in re.findall(r'\b(struct|union|enum)\s+([A-Za-z_]\w*)\s*\{', item)}
    for body in re.findall(r'\benum\b[^{;]*\{([^}]*)\}', item):  # its constants are names too
        names |= {m.group(1) for m in re.finditer(r'(?:^|,)\s*([A-Za-z_]\w*)', body)}
    flat = re.sub(r'\{[^{}]*\}', ' ', item)
    while '{' in flat:
        flat = re.sub(r'\{[^{}]*\}', ' ', flat)
    if re.fullmatch(r'\s*(struct|union|enum)\b[\w\s]*;\s*', flat):  # a type defined with no declarator after it
        return 'type', names
    if re.match(r'\s*typedef\b', item):
        pointer = re.search(r'\(\s*\*\s*([A-Za-z_]\w*)\s*\)', flat)
        words = [w for w in _IDENT.findall(re.sub(r'\[[^\]]*\]', ' ', flat.split('(')[0] if not pointer else '')) if w not in _KEYWORDS]
        names |= {pointer.group(1)} if pointer else set(words[-1:])
        if item.count('{'):
            names |= {w for w in re.findall(r'[{}]\s*([A-Za-z_]\w*)\s*[,;]', flat)}
        return 'type', names
    head = flat.split('=')[0]
    m = re.match(r'[^(]*?([A-Za-z_]\w*)\s*\((?!\s*\*)', head)  # a function: the name before its first (
    if m:
        return 'decl', {m.group(1)}
    pointer = re.search(r'\(\s*\*\s*([A-Za-z_]\w*)\s*\)', head)
    words = [w for w in _IDENT.findall(re.sub(r'\[[^\]]*\]', ' ', head)) if w not in _KEYWORDS]
    return 'decl', {pointer.group(1)} if pointer else set(words[-1:])


def missing_items(donor_header: str, header: str, rename, known) -> list[str]:
    """The donor header's types and declarations this header lacks, in the donor's order, written with this
    project's names (`rename(name)`: this project's, or None). A declaration comes only for a name this project
    has (`known(name)`); an item that still
    says an address name of the donor's (func_/D_, which didn't map) is left out, and so is anything this
    header already defines."""
    have = set().union(*(defines(i)[1] for i in header_items(header))) if header.strip() else set()
    out = []
    for item in header_items(donor_header):
        kind, names = defines(item)
        if not names:
            continue
        text = _IDENT.sub(lambda m: rename(m.group(0)) or m.group(0), item)
        names = {rename(n) or n for n in names}
        if names & have:
            continue
        if kind == 'decl' and not all(known(n) for n in names):
            continue
        if any(generated(w) and not known(w) for w in _IDENT.findall(text)):
            continue  # it mentions a donor function or global this release has no name for
        out.append(text)
        have |= names
    return out


# ------------------------------------------------------------------ bringing names over

def bring_names(net, slug: str, log) -> dict:
    """Give this project the linked release's names (functions and globals) and its header's missing types.
    `log.progress` is told each step, and how many names (and of their matches, how many) are checked so far."""
    from . import symbol_names
    progress = getattr(log, 'progress', None) or (lambda _: None)
    progress(dict(phase='Reading this project and the linked release'))  # (it waits while the project syncs)
    proj = net.project_row(slug)
    link_ = linked(net, proj)
    if not link_:
        raise NetworkError('Link this project with another release of the game first.')
    donor = net.project_row(link_['slug'])
    progress(dict(phase=f"Pairing functions with {donor['name']}"))
    log(f"Pairing {proj['name']} with {donor['name']} ({link_['region'] or 'other release'}).")
    p = plan(net, proj, donor)
    identical = sum(1 for *_, s in p.pairs if s == 1.0)
    log(f"{len(p.pairs):,} functions paired ({identical:,} identical); {len(p.addresses):,} addresses learned, "
        f"{p.addresses.conflicts} ambiguous.")
    log(f"{len(p.names):,} names to bring over; {p.kept:,} of this project's own names kept; "
        f"{p.taken:,} already used here; {p.unmapped:,} at addresses that didn't map.")
    renamed, rejected, total = 0, [], len(p.names)
    message = f"Names from {donor['name']} ({link_['region'] or 'other release'})"
    queue = [p.names[i:i + CHUNK] for i in range(0, total, CHUNK)]
    done = 0
    while queue:
        if getattr(log, 'cancel_event', None) is not None and log.cancel_event.is_set():
            break
        rows = queue.pop(0)
        what = f"{rows[0]['new']} ({done + 1:,} of {total:,})" if len(rows) == 1 else \
            f"names {done + 1:,}–{done + len(rows):,} of {total:,}"

        def checked(value, rows=rows, what=what, base=done):  # the rename's own check, as part of the whole
            n, of = value.get('done') or 0, value.get('total') or 0
            progress(dict(phase=f"Renaming {what}: checking {n:,} of {of:,} matches that use them",
                          done=base + (len(rows) * n // of if of else 0), total=total))
        checked({})
        step = lambda line: log(line)  # (symbol_names reads the progress callback from the log it's given)
        step.progress = checked
        try:
            renamed += symbol_names.apply(net, slug, rows, message, step)['renamed']
            done += len(rows)
            log(f"{done:,} of {total:,} names checked: {renamed:,} brought over, every match still matches.")
        except NetworkError as error:
            if len(rows) == 1:
                rejected.append(dict(rows[0], why=str(error).split('\n')[0]))
                done += 1
                log(f"Left out {rows[0]['new']} (for {rows[0]['old']}): {rejected[-1]['why']}")
            else:  # find the names that fail (one that breaks a match, say): the rest of the chunk still goes in
                log(f"{len(rows)} names together would break a match: trying them in halves to find which.")
                queue[:0] = [rows[:len(rows) // 2], rows[len(rows) // 2:]]
    progress(dict(phase='Renaming finished', done=done, total=total))
    log(f"{renamed:,} names brought over" + (f"; {len(rejected)} left out." if rejected else "."))
    progress(dict(phase="Pairing again with the new names, for the header's types"))
    header = _bring_header(net, proj, donor, plan(net, net.project_row(slug), donor), log)  # with the new names
    result = dict(paired=len(p.pairs), identical=identical, addresses=len(p.addresses), renamed=renamed,
                  rejected=rejected[:50], kept=p.kept, taken=p.taken, unmapped=p.unmapped, header=header, at=time.time())
    net._update_cfg(slug, lambda cfg: cfg.get('release_of', {}).__setitem__(
        'last', {k: v for k, v in result.items() if k != 'rejected'} | dict(rejected=len(rejected))))
    return result


def _bring_header(net, proj, donor, p: Plan, log) -> dict:
    """The donor's project-wide header's missing types, as one edit every matched function is checked against.
    (A donor's per-file headers stay: this project's files aren't the donor's.)"""
    if net._unit_contexts(proj['id']):
        log('Header types were not brought over: this project has a header per source file.')
        return dict(added=0, why='per-file headers')
    syms = net.symbols(proj['id'])
    produced = set()  # this project's names the translation gave (an address name resolves for any address)

    def rename(name):
        new = p.name(name)
        if new:
            produced.add(new)
        return new
    known = lambda name: name in syms.by_name or name in produced
    items = missing_items(net._context(donor['id'])['header'], net._context(proj['id'])['header'], rename, known)
    # A matched function here that declares one of these names itself (its own struct, a prototype with other
    # types) would stop compiling: those items stay out, and the edit's check covers the rest.
    from ..function_rows import selected
    store = net.project_store(proj['id'])
    ids = [r['id'] for r in store.all("SELECT id FROM functions WHERE project_id=? AND status='matched'", proj['id'])]
    used = {w for r in selected(store, proj['id'], ids) for w in _IDENT.findall(r['matched_source'] or '')}
    clashing = [i for i in items if {n.split()[-1] for n in defines(i)[1]} & used]
    items = [i for i in items if i not in clashing]
    if clashing:
        log(f"{len(clashing)} header items left out: matched functions here declare those names themselves.")
    if not items:
        log('The header already has the other release\'s types.')
        return dict(added=0)
    agent, _ = net.local_agent('you')
    block = f"/* From {donor['name']}, another release of this game. */\n" + '\n'.join(items)
    progress = getattr(log, 'progress', None) or (lambda _: None)
    log(f"Adding {len(items)} types and declarations to the header, if every matched function still matches.")

    def checked(value):
        progress(dict(phase=f"Checking {len(items)} header items against every matched function",
                      done=value.get('done') or 0, total=value.get('total') or 0))
    checked({})
    result = net.edit_context(agent, proj['slug'], '', block, f"Types and declarations from {donor['name']}",
                              progress=checked)
    log(result.split('\n')[0])
    return dict(added=len(items) if result.startswith('✅') else 0, why=None if result.startswith('✅') else result.split('\n')[0])


# ------------------------------------------------------------------ the other release's code

def transfer(net, slug: str, job=None, log=lambda *_: None) -> dict:
    """Try the linked release's matched C, with this project's names, on each paired function: an open one becomes
    a match when the judge says so (built with the other release's compiler settings for that function when its
    own don't match and those do), and a matched one not yet polished takes the other release's polished C when
    it still matches. Misses are remembered as twins' are, so a pass tries only what changed."""
    proj = net.project_row(slug)
    link_ = linked(net, proj)
    if not link_ or not _eligible(net, proj):
        return dict(matched=0, polished=0, tried=0)
    donor = net.project_row(link_['slug'])
    stop = getattr(job, 'stop', None)
    progress = getattr(log, 'progress', None) or (lambda _: None)
    p = plan(net, proj, donor)
    from ..function_rows import selected
    theirs = {r['id']: r for r in selected(net.project_store(donor['id']), donor['id'],
                                            [x.id for x, _, _ in p.pairs if x.status == 'matched'])}
    work = [(x, y, s) for x, y, s in p.pairs if x.id in theirs and theirs[x.id]['matched_source']]
    work.sort(key=lambda t: (t[1].status != 'open', -t[2]))  # open functions first, identical code first
    counts = dict(matched=0, polished=0, tried=0)
    agent, _ = net.local_agent('autopilot')
    for done, (x, y, score) in enumerate(work):
        if stop is not None and stop.is_set():
            break
        a = theirs[x.id]
        progress(dict(phase="Trying the other release's code" if y.status == 'open' else "Bringing its polished code",
                      done=done, total=len(work), donor=a['name'], function=y.name))
        b = net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=?", y.id)
        if b is None:
            continue
        source = p.text(a['matched_source'])
        if b['status'] == 'open':
            outcome = _try_open(net, agent, proj, donor, a, b, source, score)
            counts['tried'] += outcome != 'skip'
            if outcome == 'match':
                counts['matched'] += 1
                if job is not None and hasattr(job, 'cross_matched'):
                    job.cross_matched += 1
                log(f"{b['name']} matched from {a['name']} in {donor['name']}.")
        elif b['status'] == 'matched' and not b['readable'] and a['readable']:
            counts['tried'] += 1
            try:
                said = net.readability.submit(agent, slug, b['name'], source)
            except NetworkError:
                continue  # held by an agent, or changed meanwhile
            if said.startswith('✅'):
                counts['polished'] += 1
    progress(dict(phase="Trying the other release's code", done=len(work), total=len(work)))
    log(f"{counts['matched']} matched and {counts['polished']} polished from {donor['name']}; "
        f"{counts['tried']} tried.")
    return counts


def _try_open(net, agent, proj, donor, a, b, source, score) -> str:
    """match | miss | skip for the other release's C on an open function, recorded as a cross-game attempt."""
    twins = net.twins
    if twins._missed(b['id'], source, project_id=proj['id']):
        return 'skip'
    origin = f" in {donor['name']}, another release of this game"
    outcome, why = twins._attempt_twin(agent, proj, a, b, source, origin=origin, similar=score < 1.0)
    if outcome == 'miss':
        outcome = _with_their_compiler(net, agent, proj, donor, a, b, source, origin, score) or outcome
    with net.project_store(proj['id']).tx() as db:
        if outcome != 'skip':
            db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,"
                       "outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                       (proj['id'], b['id'], donor['id'], a['id'], outcome, why[:200], time.time()))
        if outcome == 'miss':
            db.execute("INSERT OR REPLACE INTO twin_misses(function_id, source_hash, project_id, at, why) "
                       "VALUES(?,?,?,?,?)", (b['id'], twins._miss_key(source), proj['id'], time.time(), why))
    return outcome


def _with_their_compiler(net, agent, proj, donor, a, b, source, origin, score) -> str | None:
    """'match' when the other release builds this function with other compiler settings and the C matches with
    them here too: they become this function's settings. The other release's match is the evidence that those
    settings built it; the judge checks they build this one."""
    from ..project import compiler_for
    from .prepared_evidence import scoped_config
    theirs = compiler_for(net.cfg(donor), a['vram'], a)
    mine = compiler_for(net.cfg(net.project_row(proj['slug'])), b['vram'], b)
    if (theirs['toolchain'], list(theirs['flags'])) == (mine['toolchain'], list(mine['flags'])):
        return None
    if net.project_store(proj['id']).one("SELECT 1 FROM claims WHERE function_id=?", b['id']):
        return None
    relation = "adapted from similar verified C in" if score < 1.0 else "identical to"
    note = f"/* {relation} {a['name']}{origin} (matched), with its names changed by decompdecomp */\n"
    from ..toolchains import ToolchainError
    try:
        ok, _, _ = net._judge_with(proj, b, theirs['toolchain'], list(theirs['flags']), note + source)
    except (NetworkError, ToolchainError):
        return None  # (a compiler this computer can't run, say: the function stays for the others)
    if not ok:
        return None
    settings = dict(toolchain=theirs['toolchain'], flags=list(theirs['flags']))
    current = net.project_row(proj['slug'])  # (the pass's row is older than the settings other matches set)
    try:
        net._apply_compiler_config(current, scoped_config(current, b, settings))
        result = net.try_quietly(agent, net.project_row(proj['slug']), b, note + source)
    except (NetworkError, ToolchainError):
        return None  # the project changed meanwhile (a helper's match): the next pass tries again
    return 'match' if result.get('matched') else None
