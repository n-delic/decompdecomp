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
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher

from ..network import IN_BUILD, NetworkError
from ..rom.symbols import AUTO_NAME
from .function_names import generated as _generated

WIDTH = 6  # the other release's code tried on this many functions at once (fewer on a computer with fewer workers)
_SETTINGS = threading.Lock()  # one compiler-settings change at a time
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


def _row(net, slug: str):
    """A project's current row without syncing its saved files first (project_row does): the other release's code
    reads rows on every try, and each sync of a finished game took seconds. Saved files follow through the progress
    watcher."""
    row = net.project_store(slug).one("SELECT * FROM projects WHERE slug=?", slug)
    if row is None:
        raise NetworkError(f"There's no project {slug!r} here any more: link another release.")
    return row


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
        other = _row(net, link['slug'])
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

def _moved_place(x: Fn, y: Fn, offset: int) -> int | None:
    """Where an instruction of x is in y, for similar code (a few instructions added or removed): in the stretches
    of instructions both have, lined up. Only for fixed-width instructions (each op is 4 bytes of the function), and
    not for the ops cut short at similarity.MAX_OPS."""
    if len(x.ops) * 4 != x.size or len(y.ops) * 4 != y.size or offset % 4:
        return None
    blocks = _matching_blocks(x.ops, y.ops)
    i = offset // 4
    for a, b, n in blocks:
        if a <= i < a + n:
            return (b + i - a) * 4 + offset % 4
    return None


_BLOCKS: dict[tuple[tuple, tuple], list] = {}


def _matching_blocks(xs: tuple, ys: tuple) -> list:
    key = (xs, ys)
    if key not in _BLOCKS:
        if len(_BLOCKS) > 4096:
            _BLOCKS.clear()
        _BLOCKS[key] = SequenceMatcher(None, xs, ys, autojunk=False).get_matching_blocks()
    return _BLOCKS[key]


class AddressMap:
    """Release A's addresses in release B, learned from identical pairs: each relocation fills in an address in
    both, and paired functions start at each other's address. An address voted two ways is dropped; one between
    two learned addresses that moved by the same amount, within MAX_SPREAD, moves with them."""

    def __init__(self, pairs: list[tuple[Fn, Fn, float]]):
        votes = defaultdict(Counter)
        for x, y, score in pairs:
            votes[x.vram][y.vram] += 1
            # a relocation at the same instruction fills in the same thing (by place, not list order: one
            # release's list can lack an entry, a %lo inside a big symbol)
            theirs = {(o, t): ay for o, t, ay in y.relocs}
            for o, t, ax in x.relocs:
                at = o if score == 1.0 else _moved_place(x, y, o)
                if at is not None and (at, t) in theirs:
                    votes[ax][theirs[(at, t)]] += 1
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
    their_address: object = None  # the other release's address for one of its names
    their_size: object = None  # the other release's size for one of its names (0: not known)
    address_of: object = None  # this project's address for one of its names
    local: dict = field(default_factory=dict)  # for one pair (for_pair): its addresses -> this project's

    def for_pair(self, x: Fn, y: Fn) -> 'Plan':
        """This plan for one pair's C: the addresses its relocations fill in, read from the instructions both have,
        before the game-wide map. Data inside one of this project's big blocks moves unevenly (the map guessed a
        variable 0x20C bytes off), but each instruction the two have in common says where its address went."""
        local = {}
        theirs = {(o, t): ay for o, t, ay in y.relocs}
        for o, t, ax in x.relocs:
            at = o if x.ops == y.ops else _moved_place(x, y, o)
            if at is not None and (at, t) in theirs:
                local.setdefault(ax, set()).add(theirs[(at, t)])
        return replace(self, local={a: next(iter(b)) for a, b in local.items() if len(b) == 1})

    def _here(self, address: int) -> int | None:
        return self.local.get(address, self.addresses.get(address))

    def start_of(self, word: str) -> int | None:
        """Where one of the other release's big variables starts here, from the fields of it this pair reaches (each
        field's address here, less its offset in the other release), as most of them say; None without such fields.
        C that reaches a big struct's fields by name never says where the struct starts: a game's 70 functions
        used its fields alone, so the address map never learned it."""
        at = self.their_address(word) if self.their_address is not None else None
        size = self.their_size(word) if self.their_size is not None and at is not None else 0
        if not size or not self.local:
            return None
        votes = Counter(b - (a - at) for a, b in self.local.items() if at <= a < at + size)
        if not votes:
            return None
        start, count = votes.most_common(1)[0]
        return start if count * 2 >= sum(votes.values()) or count >= 2 else None

    def field_moves(self, word: str) -> tuple[dict[int, int], set[int]]:
        """({offset in the other release: offset here} for the fields of one of its big variables this pair reaches
        whose offset changed, {offsets that stayed}): what regional_adapt lays the variable's struct out by."""
        at = self.their_address(word) if self.their_address is not None else None
        start = self.start_of(word) if at is not None else None
        if start is None:
            return {}, set()
        size = self.their_size(word)
        moved, kept = {}, set()
        for a, b in self.local.items():
            if at <= a < at + size:
                if b - start == a - at:
                    kept.add(a - at)
                else:
                    moved[a - at] = b - start
        return moved, kept

    def name(self, word: str) -> str | None:
        """This project's name for one of the other release's: given, or an address name (D_8009B5D0) moved as
        the address map says (inside one of this project's own symbols, the address's own name: see plan). None: no
        name here (unmapped)."""
        if self.local and self.their_address is not None:
            at = self.their_address(word)
            here = None if at is None else self.local.get(at, self.start_of(word))
            if here is not None:
                name, offset = self.name_at(here)
                return f'D_{here:08X}' if offset else name
        if word in self.translate:
            return self.translate[word]
        m = AUTO_NAME.fullmatch(word)
        if not m or self.name_at is None:
            return None
        address = self.addresses.get(int(m.group(1), 16))
        if address is None:
            return None
        name, offset = self.name_at(address)
        return f'D_{address:08X}' if offset else name

    def text(self, source: str) -> str:
        """The other release's C or header text with this project's names."""
        return _IDENT.sub(lambda m: self.name(m.group(0)) or m.group(0), source)

    def offsets(self, source: str) -> str:
        """The other release's C with `name + 0x3252` (a field of a big global reached by its byte offset) moved to
        where that field is here, as the pair's own relocations or the address map say; the rest as it was. Its names are the other release's
        still: text() changes them."""
        if self.their_address is None or self.address_of is None:
            return source

        def moved(m):
            base, number = m.group(1), m.group(2)
            start = self.their_address(base)
            here = self.address_of(self.name(base) or base)
            offset = int(number, 0)
            # only inside the symbol: `ptr + 0x245` on a pointer variable is where it points, plus 0x245
            if start is None or here is None or not (self.their_size and offset < self.their_size(base)):
                return m.group(0)
            target = self._here(start + offset)
            if target is None or target - here == offset or target < here:
                return m.group(0)
            new = target - here
            return m.group(0)[:m.start(2) - m.start()] + (f'0x{new:X}' if number.lower().startswith('0x') else str(new))

        return _GLOBAL_OFFSET.sub(moved, source)


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
        if s.address in functions_at:
            continue
        address = addresses.get(s.address)
        if address is None:
            unmapped += 0 if generated(s.name) else 1
            continue
        current, offset = syms.name_at(address)
        if s.kind in ('func', 'function'):
            # a function too small to pair (or set aside) that paired code calls or takes the address of: its C
            # says this project's name for it. Not renamed here: its own code wasn't compared.
            if not offset and syms.resolve(current) == address:
                translate[s.name] = current
            continue
        if offset:
            # inside one of this project's own symbols (a big block nobody split yet, where the other release has
            # its variables): its C reaches the address by the address's own name, which links there. Kept out of
            # the names brought over: this project's layout there isn't known. A pass over a game's other release
            # found 150 functions the same but for such names.
            translate[s.name] = f'D_{address:08X}'
            continue
        if generated(s.name):
            translate[s.name] = current
        else:
            offer(current, s.name, address)
    return Plan(pairs, addresses, rows, kept, unmapped, taken, translate, syms.name_at, donor_syms.resolve,
                lambda name: _data_size(donor_syms.by_name.get(name)), syms.resolve)


def _data_size(sym) -> int:
    """A data symbol's size, 0 for a function or an unknown one: start_of and offsets read variables only (a
    function's own name placed by the addresses its code reaches renamed the function: its C had no such function)."""
    if sym is None or sym.kind in ('func', 'function'):
        return 0
    return sym.size or 0


# ------------------------------------------------------------------ header types

_COMMENT = re.compile(r'/\*.*?\*/|//[^\n]*', re.S)
_IDENT = re.compile(r'[A-Za-z_]\w*')
_GLOBAL_OFFSET = re.compile(r'(?<![\w.>])([A-Za-z_]\w*)\s*\+\s*(0[xX][0-9a-fA-F]+|\d+)(?![\w.])')
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


_CONDITION = re.compile(r'#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)', re.S)


def built_items(text: str) -> list[str]:
    """header_items, without the ones in a conditional branch the game's own build leaves out. A header kept for a
    port too says `#ifdef PORT typedef uintptr_t AddrWord; #else typedef s32 AddrWord; #endif`: both branches
    define the name, and copying the first gave another release the PC's type, which the game's compiler can't
    build. A macro counts as defined when the header defines it before; a condition this can't read keeps
    every branch, as before."""
    defined, out, stack = set(), [], []  # stack: [taken now, a branch was taken, readable]
    for item in header_items(text):
        m = _CONDITION.match(item) if item.startswith('#') else None
        if m:
            word, rest = m.group(1), m.group(2).strip()
            if word in ('ifdef', 'ifndef', 'if'):
                value = _condition(word, rest, defined)
                stack.append([value is not False, bool(value), value is not None])
            elif stack and word == 'elif':
                value = _condition('if', rest, defined)
                top = stack[-1]
                top[2] = top[2] and value is not None
                top[0] = (not top[1] and value is not False) if top[2] else True
                top[1] = top[1] or bool(value)
            elif stack and word == 'else':
                top = stack[-1]
                top[0] = not top[1] if top[2] else True
            elif stack and word == 'endif':
                stack.pop()
            continue
        if not all(level[0] for level in stack):
            continue
        name = re.match(r'#\s*define\s+([A-Za-z_]\w*)', item)
        if name:
            defined.add(name.group(1))
        undefine = re.match(r'#\s*undef\s+([A-Za-z_]\w*)', item)
        if undefine:
            defined.discard(undefine.group(1))
        out.append(item)
    return out


def _condition(word: str, rest: str, defined: set[str]) -> bool | None:
    """Whether an #if/#ifdef/#ifndef is true for the game's build: True, False, or None when it can't be read."""
    rest = _COMMENT.sub(' ', rest).strip()
    if word in ('ifdef', 'ifndef'):
        name = _IDENT.match(rest)
        if not name:
            return None
        return (name.group(0) in defined) == (word == 'ifdef')
    m = re.fullmatch(r'(!)?\s*defined\s*\(?\s*([A-Za-z_]\w*)\s*\)?', rest)
    if m:
        return (m.group(2) in defined) != bool(m.group(1))
    if re.fullmatch(r'\d+', rest):
        return int(rest) != 0
    return None


def missing_items(donor_header: str, header: str, rename, known) -> list[str]:
    """The donor header's types and declarations this header lacks, in the donor's order, written with this
    project's names (`rename(name)`: this project's, or None). A declaration comes only for a name this project
    has (`known(name)`); an item that still
    says an address name of the donor's (func_/D_, which didn't map) is left out, and so is anything this
    header already defines."""
    have = set().union(*(defines(i)[1] for i in header_items(header))) if header.strip() else set()
    out = []
    for item in built_items(donor_header):
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
    work.sort(key=lambda t: _likeliest(*t))
    counts = dict(matched=0, polished=0, tried=0)
    agent, _ = net.local_agent('autopilot')
    lock = threading.Lock()
    active: dict[int, str] = {}
    tally = dict(done=0)

    def show(phase):
        progress(dict(phase=phase, done=tally['done'], total=len(work), donor=', '.join(d for d, _ in active.values()),
                      function=', '.join(f for _, f in active.values())))

    def one(item):
        x, y, score = item
        if stop is not None and stop.is_set():
            return
        a = theirs[x.id]
        with lock:
            active[y.id] = (a['name'], y.name)
            show("Trying the other release's code")
        try:
            b = net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=?", y.id)
            if b is not None and b['status'] == 'open' and _held(net, proj, b['id']):
                with lock:  # a helper is on it: tried again at the end of the pass, if it's free by then
                    held.append(item)
                    active.pop(y.id, None)
                    tally['done'] += 1
                return
            outcome = 'skip' if b is None or b['status'] != 'open' else \
                _try_open(net, agent, proj, donor, a, b, p.text(a['matched_source']), score)
            if outcome != 'match' and b is not None and b['status'] == 'open':
                outcome = _try_fitted(net, agent, proj, donor, p, a, b, (x, y)) or outcome
        except Exception as error:  # one odd function must not stop the rest
            logging.getLogger('decompdecomp').info('regional: %s from %s failed: %s', y.name, a['name'], error)
            outcome = 'skip'
        with lock:
            active.pop(y.id, None)
            tally['done'] += 1
            counts['tried'] += outcome != 'skip'
            if outcome == 'match':
                counts['matched'] += 1
                if job is not None and hasattr(job, 'cross_matched'):
                    job.cross_matched += 1
            show("Trying the other release's code")
        if outcome == 'match':
            log(f"{y.name} matched from {a['name']} in {donor['name']}.")

    def polish(item):
        x, y, _ = item
        if stop is not None and stop.is_set():
            return
        a = theirs[x.id]
        with lock:
            active[y.id] = (a['name'], y.name)
            show("Bringing its polished code")
        said = ''
        try:
            b = net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=?", y.id)
            if b is not None and b['status'] == 'matched' and not b['readable'] and a['readable']:
                with lock:
                    counts['tried'] += 1
                said = net.readability.submit(agent, slug, b['name'], p.text(a['matched_source']), synchronize=False)
        except NetworkError:
            pass  # held by an agent, or changed meanwhile
        except Exception as error:  # one odd function must not stop the rest
            logging.getLogger('decompdecomp').info('regional: polished %s from %s failed: %s', y.name, a['name'], error)
        with lock:
            active.pop(y.id, None)
            tally['done'] += 1
            counts['polished'] += said.startswith('✅')
            show("Bringing its polished code")

    # Side by side: nearly all of a try is the compiler, and the machine has cores to spare (the shared compiler
    # slots keep agents' compiles going meanwhile). One at a time took 20 minutes on a game's 1,968 pairs, and its
    # polished code 12 s a function. Open functions first, then polished code for the matched ones.
    from . import workers
    from .purpose_work import Executor
    open_work = [t for t in work if t[1].status == 'open']
    held: list = []
    for run, items in ((one, open_work), (polish, work[len(open_work):])):
        with Executor(max_workers=max(1, min(WIDTH, workers.WORKERS)), purpose="Other release's code",
                      project=slug, stop=stop.set if stop is not None else None) as pool:
            list(pool.map(run, items))
            if run is one and held:  # what helpers held when the pass reached it, once more
                again, held[:] = list(held), []
                tally['done'] -= len(again)
                list(pool.map(run, again))
    counts['held'] = len(held)
    if held:
        log(f"{len(held)} functions were held by helpers and weren't tried: Try its code again when they're free.")
    progress(dict(phase="Trying the other release's code", done=len(work), total=len(work)))
    log(f"{counts['matched']} matched and {counts['polished']} polished from {donor['name']}; "
        f"{counts['tried']} tried.")
    return counts


def _likeliest(x: Fn, y: Fn, score: float) -> tuple:
    """Open functions first; then those whose bytes are the same in both releases apart from the addresses they
    fill in (the same twin key), whose other C matches unless the compiler differs; then by similarity. Pairs scored
    1.0 for the same instructions can still differ in their operands (a struct field at 0x42 in one release, 0x4c
    in the other), and those rarely match as they are."""
    return (y.status != 'open', x.key != y.key, -score)


def _held(net, proj, function_id: int) -> bool:
    """Whether a helper holds the function now (an unexpired claim): its own work comes first."""
    return net.project_store(proj['id']).one("SELECT 1 FROM claims WHERE function_id=? AND expires_at > ?",
                                             function_id, time.time()) is not None


def _fitted_sources(net, proj, donor, p, a, b, pair=None) -> tuple[list[str], str] | None:
    """(the other release's C fitted to this release's numbers, most changed first; the header it builds against):
    regional_adapt's versions (its own structs laid out as this release reads them, offsets, constants and floats
    that changed, read from the instructions both have), Plan.offsets' (fields of a big global reached by byte
    offset), each with the declarations it needs. None when the instructions can't be read."""
    from .. import scoring
    from . import regional_adapt
    try:
        old = scoring.disassemble(net._target_obj(a, donor))
        new = scoring.disassemble(net._target_obj(b, proj))
    except (NetworkError, ValueError, KeyError):
        return None
    header = net._context(proj['id'], b['unit'])['header']
    plain = p.text(a['matched_source'])
    own = p.for_pair(*pair) if pair else p
    moved = own.text(own.offsets(a['matched_source']))
    tries = [moved] if moved != plain else []
    fields = ({}, set())
    for word in set(_IDENT.findall(a['matched_source'])):
        moved_fields, kept_fields = own.field_moves(word)
        for u, v in moved_fields.items():
            fields[0].setdefault(u, v)
        fields[1].update(kept_fields)
    for base in (moved, plain) if moved != plain else (plain,):
        tries += [v for v in regional_adapt.variants(net, proj, header, base, b, old, new, fields) if v not in tries]
    declared = _their_declarations(net, donor, a, own, header, moved)
    if declared:  # without them none of these builds: each takes them
        tries = [declared + '\n' + t for t in ([plain] if moved == plain else []) + tries]
    return tries[:regional_adapt.MAX_VARIANTS + 1], header


def _try_fitted(net, agent, proj, donor, p, a, b, pair=None) -> str | None:
    """'match' or 'miss' for the other release's C fitted to this release's numbers (_fitted_sources), then edited
    where the releases' code still differs (regional_edits); None when nothing differs that the C could change."""
    found = _fitted_sources(net, proj, donor, p, a, b, pair)
    if found is None:
        return None
    tries, header = found
    outcome = None
    for source in tries:
        result = _try_open(net, agent, proj, donor, a, b, source, 1.0, how=', fitted to this release\'s offsets')
        if result == 'match':
            return 'match'
        outcome = outcome or (result if result != 'skip' else None)
    from . import regional_edits
    plain = p.text(a['matched_source'])
    starts = tries + ([plain] if plain not in tries else [])
    edited = regional_edits.search(net, proj, header, starts, b)
    for source in ([edited[0]] + edited[2]) if edited else []:
        if source in starts:
            continue
        result = _try_open(net, agent, proj, donor, a, b, source, 1.0,
                           how=', edited where this release\'s code differs')
        if result == 'match':
            return 'match'
        outcome = outcome or (result if result != 'skip' else None)
    return outcome


def _their_declarations(net, donor, a, own, header, source) -> str:
    """The other release's header declarations (and the types they need) for the names this C uses that neither
    this project's header nor the C declares, written with this project's names; '' when it needs none. Its header
    can declare what the function's C left to it (a global, a prototype), and this project's header can lack it."""
    def declared(text):
        return set().union(set(), *(defines(i)[1] for i in header_items(text))) if text.strip() else set()
    have = {n.split()[-1] for n in declared(header) | declared(source)}
    used = set(_IDENT.findall(_COMMENT.sub(' ', source))) - _KEYWORDS
    if not used - have:
        return ''
    items = [(i, {n.split()[-1] for n in defines(i)[1]})
             for i in missing_items(net._context(donor['id'], a['unit'])['header'], header, own.name, lambda _: True)]
    wanted, out = used - have, []
    for _ in range(3):  # what they need in turn: a type a declaration names
        found = [(i, names) for i, names in items if names & wanted and i not in out]
        if not found:
            break
        out += [i for i, _ in found]
        wanted = set().union(*(set(_IDENT.findall(i)) for i, _ in found)) - have - _KEYWORDS
    if not out:
        return ''
    order = {i: k for k, (i, _) in enumerate(items)}
    return '\n'.join(sorted(out, key=order.get))


def _try_open(net, agent, proj, donor, a, b, source, score, how='') -> str:
    """match | miss | skip for the other release's C on an open function, recorded as a cross-game attempt."""
    twins = net.twins
    missed = twins._missed(b['id'], source, project_id=proj['id'])
    same = missed and _same_settings(net, proj, donor, a, b)
    again = source + (_SAME_SETTINGS if same else _THEIR_SETTINGS)
    if missed and twins._missed(b['id'], again, project_id=proj['id']):
        return 'skip'
    origin = f" in {donor['name']}, another release of this game{how}"
    theirs = None
    if same:
        # Its settings are the other release's already, and the remembered miss may be from before they were: the
        # C gets one try with them. A try that went stale once left the other release's settings on two functions
        # of an N64 game; the pass then had no other settings to try and skipped the plain try as missed.
        outcome, why = twins._attempt_twin(agent, proj, a, b, source, origin=origin, similar=score < 1.0)
    elif missed:
        # A miss another matcher remembers (twins, similar code) was built with this project's settings only: the
        # other release's settings still get their one try. Skipping it left six library functions of an N64
        # game open that matched with the other release's flags once nothing else had tried their C first.
        theirs = _with_their_compiler(net, agent, proj, donor, a, b, source, origin, score)
        outcome = theirs or 'miss'
        why = "with the other release's compiler settings"
    else:
        outcome, why = twins._attempt_twin(agent, proj, a, b, source, origin=origin, similar=score < 1.0)
        theirs = _with_their_compiler(net, agent, proj, donor, a, b, source, origin, score) \
            if outcome == 'miss' else None
        if theirs == 'match':
            outcome = 'match'
    with net.project_store(proj['id']).tx() as db:
        if outcome != 'skip':
            db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,"
                       "outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                       (proj['id'], b['id'], donor['id'], a['id'], outcome, why[:200], time.time()))
        if outcome == 'miss':
            if missed:
                keys = () if theirs == 'skip' else (again,)  # (unsure: tried again next pass)
            else:
                keys = (source,) if theirs == 'skip' else (source, source + _THEIR_SETTINGS)
            for key in keys:
                db.execute("INSERT OR REPLACE INTO twin_misses(function_id, source_hash, project_id, at, why) "
                           "VALUES(?,?,?,?,?)", (b['id'], twins._miss_key(key), proj['id'], time.time(), why))
    return outcome


# Added to a source's miss key once the other release's compiler settings were tried on it too. (Its first wording
# also marked builds another job's settings change outdated as misses: those don't count.)
_THEIR_SETTINGS = "\n/* with the other release's compiler settings, built to the end */"


# Added instead when its settings were already the other release's, and its C was tried with them.
_SAME_SETTINGS = "\n/* with the other release's compiler settings, already this function's */"


def _same_settings(net, proj, donor, a, b) -> bool:
    """Whether this function is built with the settings the other release builds its twin with."""
    from ..project import compiler_for
    theirs = compiler_for(net.cfg(donor), a['vram'], a)
    mine = compiler_for(net.cfg(_row(net, proj['slug'])), b['vram'], b)
    return (theirs['toolchain'], list(theirs['flags'])) == (mine['toolchain'], list(mine['flags']))


def _with_their_compiler(net, agent, proj, donor, a, b, source, origin, score) -> str | None:
    """'match' when the other release builds this function with other compiler settings and the C matches with
    them here too: they become this function's settings. The other release's match is the evidence that those
    settings built it; the judge checks they build this one. 'skip' when it builds but the try couldn't finish
    (the project's settings changed meanwhile, a helper took the function): not a miss, so the next pass tries
    again. None otherwise."""
    from ..project import compiler_for
    from .prepared_evidence import scoped_config
    theirs = compiler_for(net.cfg(donor), a['vram'], a)
    mine = compiler_for(net.cfg(_row(net, proj['slug'])), b['vram'], b)
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
    for _ in range(STALE_TRIES):
        with _SETTINGS:  # tries run side by side: each reads the settings it changes after the last one's change
            current = _row(net, proj['slug'])  # (the pass's row is older than the settings other matches set)
            try:
                net._apply_compiler_config(current, scoped_config(current, b, settings))
                result = net.try_quietly(agent, _row(net, proj['slug']), b, note + source, synchronize=False)
            except NetworkError as error:
                # Another job changed the project's settings while this built (similar-code reuse's compiler
                # trials run beside the pass): the build was right and only went stale, so it's built again.
                # Recorded as a miss, it left two functions of an N64 game open that match one at a time.
                if str(error).startswith(_STALE):
                    continue
                break  # (a helper's claim, say)
            except ToolchainError:
                break
        return 'match' if result.get('matched') else None
    # Unsure: its own settings come back, so the next pass sees the settings still differ and tries them again.
    with _SETTINGS:
        try:
            current = _row(net, proj['slug'])
            net._apply_compiler_config(current, scoped_config(
                current, b, dict(toolchain=mine['toolchain'], flags=list(mine['flags']))))
        except (NetworkError, ToolchainError):
            pass
    return 'skip'


STALE_TRIES = 3
_STALE = 'Compiler settings changed while'  # network.py's message for a build the settings outdated


# ------------------------------------------------------------------ the other release in a helper's brief

BRIEF_TTL = 600  # seconds a brief's release plan is kept (names change as helpers rename; layouts by key)
BRIEF_SOURCE = 6000  # characters of the other release's C a brief shows at most
BRIEF_ROWS = 40  # instruction differences a brief shows at most
_plans: dict = {}
_plans_lock = threading.Lock()


def _brief_plan(net, proj, donor) -> Plan:
    key = (proj['id'], donor['id'], net._layout_key(proj), net._layout_key(donor))
    with _plans_lock:
        found = _plans.get(key)
        if found and time.monotonic() - found[0] < BRIEF_TTL:
            return found[1]
    p = plan(net, proj, donor)
    with _plans_lock:
        for old in [k for k in _plans if k[:2] == key[:2]]:
            del _plans[old]
        _plans[key] = (time.monotonic(), p)
    return p


def brief(net, proj, fn) -> list[str]:
    """For an open function's brief, when its project is linked with another release that matched the function's
    pair: that C with this release's names, offsets and declarations, and where the two releases' instructions
    differ. What Try its code couldn't match is real change between the releases (a call added, a branch moved): the
    helper starts from the other release's C and makes just that change."""
    if fn['status'] != 'open':
        return []
    link_ = linked(net, proj)
    if not link_:
        return []
    try:
        donor = _row(net, link_['slug'])
        p = _brief_plan(net, proj, donor)
    except (NetworkError, KeyError, ValueError):
        return []
    found = next(((x, y, s) for x, y, s in p.pairs if y.id == fn['id']), None)
    if found is None:
        return []
    x, y, score = found
    from ..function_rows import selected
    rows = selected(net.project_store(donor['id']), donor['id'], [x.id])
    a = rows[0] if rows else None
    if a is None or a['status'] != 'matched' or not a['matched_source']:
        return []
    own = p.for_pair(x, y)
    source = own.text(own.offsets(a['matched_source']))
    header = net._context(proj['id'], fn['unit'])['header']
    declared = _their_declarations(net, donor, a, own, header, source)
    alike = 'the same instructions' if score == 1.0 else f'{score * 100:.0f}% the same instructions'
    out = ["", f"### Another release of this game: {donor['name']} matched this function as {a['name']} ({alike})",
           "Its C with this release's names, offsets and declarations. Where it still differs is a change between the "
           "releases: start from this and make that change."]
    text = (declared + '\n' + source if declared else source).strip()
    if len(text) <= BRIEF_SOURCE:
        out += ["```c", text, "```"]
    else:
        out.append(f"(its C is {len(text):,} characters: read {a['name']} in {donor['slug']} with get_function)")
    out += _instruction_changes(net, proj, donor, a, fn)
    return out


_RELOCATED = re.compile(r'%\w+\([^)]*\)|\b(?:func|D|jtbl|L)_[0-9A-Fa-f]{8}\b')


def _instruction_changes(net, proj, donor, a, fn) -> list[str]:
    """Where the two releases' machine code differs, lined up (regional_adapt.align): instructions only one has, and
    those whose operands differ but for the addresses they name."""
    from .. import scoring
    from .regional_adapt import align
    try:
        old = scoring.disassemble(net._target_obj(a, donor))
        new = scoring.disassemble(net._target_obj(fn, proj))
    except (NetworkError, ValueError, KeyError):
        return []
    rows = align(old, new)
    lines = []
    seen_old, seen_new = {i for i, _ in rows}, {j for _, j in rows}
    events = [(j, f"- here only: `{new[j].mnemonic} {new[j].operands}`") for j in range(len(new)) if j not in seen_new]
    events += [(next((jj for ii, jj in rows if ii > i), len(new)),
                f"- {donor['name']} only: `{old[i].mnemonic} {old[i].operands}`")
               for i in range(len(old)) if i not in seen_old]
    def plain(ins):  # addresses aside; a branch or jump's target moves with the code around it
        text = _RELOCATED.sub('@', ins.operands)
        return re.sub(r'[^,\s]+$', '@', text) if ins.mnemonic.startswith(('b', 'j')) else text

    for i, j in rows:
        if plain(old[i]) != plain(new[j]):
            events.append((j, f"- at +0x{new[j].offset:X}: here `{new[j].mnemonic} {new[j].operands}`, "
                              f"{donor['name']} `{old[i].mnemonic} {old[i].operands}`"))
    if not events:
        return []
    events.sort(key=lambda e: e[0])
    lines = ["", f"Instructions that differ from {donor['name']}'s ({len(events)}, in this release's order; "
                 "addresses aside):"]
    lines += [text for _, text in events[:BRIEF_ROWS]]
    if len(events) > BRIEF_ROWS:
        lines.append(f"- … and {len(events) - BRIEF_ROWS} more")
    return lines
