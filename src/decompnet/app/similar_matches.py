"""A bounded, token-free search using verified C from similar functions in other ROM projects.

Mnemonic similarity is only a shortlist. Names come from aligned relocations or verified regional pairs; every
candidate goes through the ordinary compiler/link judge. Small immediate differences can suggest literal
edits; they never change the target or count as evidence of a match on their own.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import replace
from difflib import SequenceMatcher
from functools import lru_cache

from .. import permuter, similarity, toolchains
from ..arch import mips_immediates
from ..metadata_rows import rows as metadata_rows
from ..project import compiler_for

MIN_BYTES, MAX_BYTES = 32, 3200
MIN_SIMILARITY = .9
MAX_CANDIDATES = 3
MAX_POOL = 400
MANUAL_SIZE_RATIO = .75
MANUAL_SIMILARITY = .76
OTHER_COMPILER_POOL = 80
MANUAL_CANDIDATES = 8
MANUAL_OTHER_COMPILERS = 3
MANUAL_REPAIRS = 16
MAX_ATTEMPTS = 128
MAX_SECONDS = 120
MAX_REPAIRS = 8
REPAIR_PERCENT = 90  # 318 open N64 functions had donor C compiling to 90-95%: worth the repair families
MUTATION_DRAWS = 48
# Consume comments, strings and identifiers whole, so edits never touch a symbol's address or documentation.
TOKENS = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
                    r'[A-Za-z_]\w*|(?<![\w.])(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:[uUlL]*)(?![\w.])', re.S)
INTEGER = re.compile(r'(0[xX][0-9a-fA-F]+|[0-9]+)([uUlL]*)\Z')


def relocations(fn) -> list[tuple[int, int, int]]:
    return sorted(tuple(int(v) for v in r[:3]) for r in json.loads(fn['relocs'] or '[]') if len(r) >= 3)


def _table_macro_edits(source, changes):
    """Recognise one-use IDs in explicitly declared 32-bit tables, never general scaled arithmetic."""
    from ..forks.csource import _blank

    if not changes:
        return {}
    clean = _blank(source)
    tables = set(re.findall(
        r"\bextern\s+(?:char\s*\*\s*\*\s*|(?:int|unsigned\s+int|u32|s32|uint32_t|int32_t)\s+\*\s*)(\w+)\s*;",
        clean))
    tables.update(re.findall(
        r"\bextern\s+(?:int|unsigned\s+int|u32|s32|uint32_t|int32_t)\s+(\w+)\s*\[\s*\]\s*;",
        clean))
    definitions = list(re.finditer(
        r"^[ \t]*#define[ \t]+([A-Za-z_]\w*)[ \t]+(0[xX][0-9a-fA-F]+|[0-9]+)([uUlL]*)[ \t]*$",
        clean, re.M))
    result = {}
    for old, new in changes.items():
        if old % 4 or new % 4:
            continue
        candidates = [m for m in definitions if int(m[2], 0 if m[2].lower().startswith('0x') else 10) == old // 4]
        if len(candidates) != 1:
            continue
        definition = candidates[0]
        name = definition[1]
        # Redefinitions and uses in control flow or other expressions make the scale ambiguous.
        uses = [m for m in re.finditer(r'\b' + re.escape(name) + r'\b', clean)
                if not definition.start() <= m.start() < definition.end()]
        if len(uses) != 1:
            continue
        indexed = any(re.search(r'\b' + re.escape(table) + r'\s*\[\s*' + re.escape(name) + r'\s*\]', clean)
                      for table in tables)
        if indexed:
            result[definition.start(2)] = (definition.end(2), str(new // 4))
    return result


def literal_variants(source: str, a, b, big: bool) -> list[str]:
    """Try at most two unambiguous literal substitutions supported by aligned immediate instructions.

    Only singleton, nonnegative C literals or one-use IDs in explicit 32-bit tables are changed.
    Repeated numbers, signed expressions, split address constants and changed instruction layouts need
    more context than this small search can establish.
    """
    changes, offset_ops = literal_changes(a, b, big)
    if not changes:
        return []
    occurrences = {}
    for token in TOKENS.finditer(source):
        if m := INTEGER.fullmatch(token.group()):
            value = int(m[1], 16 if m[1].lower().startswith('0x') else 10)
            occurrences.setdefault(value, []).append((token, m[2]))
    replacements = {}
    for old, new in changes.items():
        hits = occurrences.get(old, [])
        if len(hits) != 1:
            continue
        token, suffix = hits[0]
        if source[:token.start()].rstrip().endswith('-'):
            continue
        replacements[token.start()] = (token.end(), str(new) + suffix)
    scaled = {old: new for old, new in changes.items()
              if offset_ops[old] <= {35, 43} and not occurrences.get(old)}
    replacements.update(_table_macro_edits(source, scaled))
    if not replacements:
        return []
    result = source
    for start, (end, value) in sorted(replacements.items(), reverse=True):
        result = result[:start] + value + result[end:]
    return [result]


def literal_changes(a, b, big):
    """Identify small standalone immediate changes before reading the donor C."""
    left, right = a['target'] or b'', b['target'] or b''
    relocated = {off for off, _, _ in relocations(a)} | {off for off, _, _ in relocations(b)}
    return mips_immediates.changes(left, right, relocated, big)


def _mnemonics(net, proj, fn) -> list[str]:
    # App bundles built before processor variants use the three-argument reader. Keep testing those installs
    # possible without replacing their other local fixes with an entire newer runtime.
    cpu = getattr(net.console(proj), 'cpu_variant', None)
    args = (fn['target'], bool(proj['big_endian']), fn['vram'] or 0)
    return similarity.mips_mnemonics(*args) if cpu is None else similarity.mips_mnemonics(*args, cpu)


@lru_cache(maxsize=256)
def _toolchain(profile: str) -> str | None:
    return json.loads(profile).get('toolchain')


# Flags that change what a compiler says, never the code it makes: warnings, defines and comment syntax.
_SILENT = ('-woff', '-W', '-w', '-fullwarn', '-D', '-U', '-Xcpluscomm', '-I', '-nostdinc')
_TAKES_VALUE = {'-woff', '-G', '-D', '-U', '-I'}


def codegen_key(profile) -> str:
    """A compiler profile as the code it makes: its toolchain and the flags that change code, in a fixed order.
    Two IDO profiles of one game differing only by `-woff 649,838,712` or `-DNON_MATCHING` were different
    compilers to the shortlist (47% of close donors passed exact equality; 75% shared the toolchain)."""
    return _codegen(profile if isinstance(profile, str) else json.dumps(profile or {}, sort_keys=True))


@lru_cache(maxsize=1024)
def _codegen(profile: str) -> str:
    value = json.loads(profile)
    if value.get('cmd'):
        return json.dumps(value, sort_keys=True)
    flags, items, pending = list(value.get('flags') or []), [], None
    for flag in flags:
        if pending is not None:
            items.append((pending, flag))
            pending = None
        elif flag in _TAKES_VALUE:
            pending = flag
        else:
            items.append((flag, ''))
    kept = sorted(item for item in items if not (item[0].startswith(_SILENT)
                                                 and not item[0].startswith(('-Wa,', '-Wab,', '-Wb,', '-Wl,'))))
    return json.dumps([value.get('toolchain'), kept])


def _aligned_prints(a, b, pa, pb, ops_a, ops_b):
    """Pair same-kind relocations in contiguous runs of aligned MIPS instructions."""
    other = {(offset, kind): address for offset, kind, address in relocations(b)}
    left, right = [], []
    source = relocations(a)
    for block in SequenceMatcher(None, ops_a, ops_b, autojunk=False).get_matching_blocks():
        # One coincidental load/call between changed instructions is weak evidence.
        if block.size < 3:
            continue
        for offset, kind, address in source:
            index, remainder = divmod(offset, 4)
            if block.a <= index < block.a + block.size:
                target = ((index + block.b - block.a) * 4 + remainder, kind)
                if target in other:
                    left.append(address)
                    right.append(other[target])
    return replace(pa, addrs=left), replace(pb, addrs=right)


def _rank_donors(pool, ops, ops_cache, b, layout, big, floor):
    from .parameter_transfers import scale_changes
    by_id = {str(a['id']): a for a in pool}
    ranked = similarity.rank(ops, {key: ops_cache[a['id']] for key, a in by_id.items()}, top=MAX_POOL, floor=floor)
    ranked.sort(key=lambda item: (not scale_changes(by_id[item[0]], b, big),
                                  [(o, t) for o, t, _ in relocations(by_id[item[0]])] != layout, -item[1]))
    return ranked


def repair_variants(source: str, feedback: dict | None = None) -> list[str]:
    """A reproducible small sample of the existing local permuter; never a new AI request."""
    rng = random.Random(hashlib.sha256(source.encode()).digest())
    seen, result = {source}, []
    def protected(text):
        return [m.group() for m in TOKENS.finditer(text) if m.group().startswith(('/*', '//', '"', "'"))]
    original = protected(source)
    markers = {row[0] for row in (feedback or {}).get('rows', []) if row[0] != ' '}
    operations = []
    if markers & {'r', 's'}:
        operations += [permuter._shuffle_declarations, permuter._toggle_register, permuter._retype_local]
    if markers & {'i', '|'}:
        operations += [permuter._add_cast, permuter._rewrite, permuter._retype_local]
    if markers & {'o', '<', '>'}:
        operations += [permuter._swap_statements, permuter._shuffle_declarations, permuter._rewrite]
    for _ in range(MUTATION_DRAWS):
        if operations:
            lines = rng.choice(operations)(source.split('\n'), rng)
            variant = '\n'.join(lines) if lines else None
        else:
            variant = permuter.mutate(source, rng)
        if variant and variant not in seen and protected(variant) == original:
            seen.add(variant)
            result.append(variant)
            if len(result) == MAX_REPAIRS:
                break
    return result


def deeper_variants(source, header, settings, function, deadline, count=MAX_REPAIRS // 2):
    """Use the already-installed full permuter for a small batch, inside this pass's time budget."""
    from .. import seeds
    from .permute import GENERATOR_SECONDS, Generator, available, weight_family

    if not available() or deadline - time.monotonic() < GENERATOR_SECONDS:
        return []
    generator = None
    try:
        generator = Generator()
        return generator.batch({'header': seeds.c_only(header), 'source': source, 'function': function,
                                'family': weight_family({'compiler': settings}, None, False),
                                'seed': int(hashlib.sha256(source.encode()).hexdigest()[:7], 16),
                                'count': count}) or []
    except OSError:
        return []  # the small built-in rewrites remain available when the optional tool cannot start
    finally:
        if generator:
            generator.close()


def regional_names(twins, proj, other) -> dict[str, str]:
    """Learn names only from accepted transfers still matched on both sides; discard conflicting evidence.

    A function pair proves its own names. Aligned relocation layouts additionally suggest data/callee names.
    These remain proposals: even a learned name must survive the destination's ordinary byte judge.
    """
    from . import repair_mappings
    from .twins import rom_renames

    net = twins.net
    left, right = twins.prints(other), twins.prints(proj)
    syms_a, syms_b = net.symbols(other['id']), net.symbols(proj['id'])
    votes = {}
    rows = net.project_store(proj['id']).all("SELECT DISTINCT source_function_id,function_id FROM cross_game_attempts "
                         "WHERE project_id=? AND source_project_id=? AND outcome='match'", proj['id'], other['id'])
    old_pairs = {(r['source_function_id'], r['function_id']) for r in rows}
    rows = [*rows, *repair_mappings.pairs(net, proj, other)]
    support = {}
    functions = set()
    for row in rows:
        a = net.project_store(other['id']).one("SELECT * FROM functions WHERE id=? AND status='matched'", row['source_function_id'])
        b = net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=? AND status='matched'", row['function_id'])
        if not a or not b or a['project_id'] != other['id'] or b['project_id'] != proj['id']:
            continue
        functions.add((a['name'], b['name']))
        names = {a['name']: b['name']}
        if a['id'] in left and b['id'] in right and (
                [(o, t) for o, t, _ in relocations(a)] == [(o, t) for o, t, _ in relocations(b)]):
            source_symbols = syms_a if (a['id'], b['id']) in old_pairs else repair_mappings.source_symbols(syms_a, a['matched_source'] or '')
            names.update(rom_renames(source_symbols, a['matched_source'] or '', left[a['id']], right[b['id']],
                                     a['name'], b['name'], syms_b) or {})
        for old, new in names.items():
            votes.setdefault(old, set()).add(new)
            support.setdefault((old,new), set()).add((a['id'],b['id']))
    def supported(old, new):
        evidence = support[old,new]
        return ((old,new) in functions or bool(evidence & old_pairs) or
                (len({a for a,b in evidence}) >= 2 and len({b for a,b in evidence}) >= 2))
    names = {old: next(iter(values)) for old, values in votes.items() if len(values) == 1
             and supported(old, next(iter(values)))}
    # Many-to-one evidence is ambiguous too (identical wrappers are common).
    return {old: new for old, new in names.items() if list(names.values()).count(new) == 1}


def trial_key(source, header, compiler, fn, names) -> str:
    """Header/type, symbol, target and compiler changes invalidate failed work immediately."""
    payload = [source, header, compiler, (fn['target'] or b'').hex(), fn['relocs'], fn['vram'], names]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


SEARCHED = ('profile', 'literals')  # trial kinds that run lookalike_profiles' compile-guided literal search


def _noted(candidate, a, other, proj):
    origin = '' if other['id'] == proj['id'] else f" in {other['name']}"
    return f"/* adapted from similar verified C in {a['name']}{origin} (matched), with its names changed by decompdecomp */\n" + candidate


def _measured(report, b):
    """The plain transfer's compiled body as a search's starting point, so it isn't compiled again."""
    from .lookalike_profiles import Measure, differing
    if not report or not report.get('candidate_target') or report.get('score') is None:
        return None
    code = bytes.fromhex(report['candidate_target'])
    relocated = ({int(r[0]) for r in json.loads(report.get('candidate_relocs') or '[]')}
                 | {int(r[0]) for r in json.loads(b['relocs'] or '[]')})
    return Measure(bool(report.get('matched')), report['score'], code, relocated,
                   differing(code, b['target'] or b'', relocated))


def donor_types(header_a: str, header_b: str, names: dict, syms_b) -> str:
    """The donor header's types and declarations the destination's header lacks, written with the destination's
    names (regional.missing_items, as another release's header is brought over), as a prelude for one try."""
    from .regional import missing_items
    if not header_a.strip():
        return ''
    known = set(getattr(syms_b, 'by_name', {}) or {}) | set(names.values())
    try:
        items = missing_items(header_a, header_b, names.get, known.__contains__)
    except (ValueError, IndexError, re.error):
        return ''
    return '\n'.join(items) + '\n' if items else ''


TRANSFERS = ('transfer', 'parameter transfer', 'typed transfer', 'fields', 'consensus')


def _skeleton(source: str) -> tuple[list, list]:
    """(the source's tokens with each integer literal as '#', [(start, end, value, suffix)] of those literals)."""
    shape, literals = [], []
    for token in TOKENS.finditer(source):
        text = token.group()
        if text.startswith(('/*', '//')):
            continue
        m = INTEGER.fullmatch(text)
        if m:
            shape.append('#')
            literals.append((token.start(), token.end(), int(m[1], 16 if m[1].lower().startswith('0x') else 10), m[2]))
        else:
            shape.append(text)
    return shape, literals


def consensus(source: str, others: list[str], report: dict, b, big: bool) -> str:
    """`source` with the integer literals that differ among the donors (written alike otherwise) set to the values the
    target's instructions have for them: a literal the games agree on stays, even where its value also appears among
    the ones that change, which a search over every literal could not tell apart. '' when nothing is settled."""
    if not report or not report.get('candidate_target'):
        return ''
    shape, literals = _skeleton(source)
    alike = [lits for text in others for other_shape, lits in [_skeleton(text)] if other_shape == shape]
    if not alike:
        return ''
    varying = [i for i, (_, _, value, _) in enumerate(literals) if any(lits[i][2] != value for lits in alike)]
    if not varying:
        return ''
    compiled = bytes.fromhex(report['candidate_target'])
    relocated = ({int(r[0]) for r in json.loads(report.get('candidate_relocs') or '[]')}
                 | {offset for offset, _, _ in relocations(b)})
    changes: dict[int, set] = {}
    for old, new in mips_immediates.value_pairs(compiled, b['target'] or b'', relocated, big):
        changes.setdefault(old, set()).add(new)
    edits = []
    for i in varying:
        start, end, value, suffix = literals[i]
        news = changes.get(value)
        if news and len(news) == 1 and (new := next(iter(news))) >= 0:
            edits.append((start, end, (hex(new) if source[start:end].lower().startswith('0x') else str(new)) + suffix))
    if not edits:
        return ''
    out = source
    for start, end, text in sorted(edits, reverse=True):
        out = out[:start] + text + out[end:]
    return out


def field_variants(source: str, header: str, report: dict, b, big: bool) -> list[str]:
    """`source` with the struct fields its compiled body (`report`) puts at other offsets than the target moved there."""
    from decomprepair import field_offsets
    if not report or not report.get('candidate_target'):
        return []
    compiled = bytes.fromhex(report['candidate_target'])
    relocated = ({int(r[0]) for r in json.loads(report.get('candidate_relocs') or '[]')}
                 | {offset for offset, _, _ in relocations(b)})
    offsets = field_offsets.pairs(compiled, b['target'] or b'', relocated, big)
    return field_offsets.from_pairs(source, header, offsets) if offsets else []


STEP_NAMES = {
    'transfer': 'Donor C as it is', 'parameter transfer': 'Donor C with this function\'s parameters',
    'typed transfer': 'With the donor\'s missing types', 'fields': 'Struct fields moved',
    'consensus': 'Numbers that differ between donors', 'profile': 'With the donor\'s compiler profile',
    'literals': 'Constants searched with the compiler', 'repair': 'Repairs of a close miss',
    'compiler': 'Donor compiler (older review leads)',
}
CLOSE_PERCENT = 90
CLOSE_LISTED = 500


def step_report(net, proj) -> dict:
    """What each step of the similar-code pass has done for this game: checks, matches, close misses (a function
    still open whose C under that step compiled to at least CLOSE_PERCENT of the target) and builds that failed.
    Read from the recorded attempts, so it is the same after a restart."""
    store = net.project_store(proj['id'])
    rows = store.all("SELECT a.function_id,a.outcome,a.reason,a.source_project_id,a.source_function_id,f.name,"
                     "f.status,f.n_insns FROM cross_game_attempts a JOIN functions f ON f.id=a.function_id "
                     "WHERE a.project_id=? AND a.reason LIKE 'similar code%'", proj['id'])
    steps, close = {}, {}
    for row in rows:
        m = re.match(r'similar code \((\d+)%\); ([^;]+);', row['reason'] or '')
        kind = m.group(2).strip() if m else 'other'
        step = steps.setdefault(kind, {'step': kind, 'label': STEP_NAMES.get(kind, kind), 'checks': 0,
                                       'functions': set(), 'matched': set(), 'close': set(), 'unbuilt': 0})
        if row['outcome'] == 'skip':
            continue
        step['checks'] += 1
        step['functions'].add(row['function_id'])
        if row['outcome'] == 'match':
            step['matched'].add(row['function_id'])
        score = re.search(r'score (\d+)(?:\D|$)', row['reason'] or '')
        if row['outcome'] == 'miss' and not score and 'build' in (row['reason'] or ''):
            step['unbuilt'] += 1
        if row['outcome'] == 'miss' and score and row['status'] == 'open' and row['n_insns']:
            percent = 100 * (1 - int(score.group(1)) / (row['n_insns'] * 100))
            if percent >= CLOSE_PERCENT:
                step['close'].add(row['function_id'])
                best = close.get((kind, row['function_id']))
                if best is None or percent > best['percent']:
                    close[(kind, row['function_id'])] = {
                        'step': kind, 'function': row['name'], 'percent': round(percent, 1),
                        'similarity': int(m.group(1)) if m else None,
                        'donor_project': row['source_project_id'], 'donor_function': row['source_function_id']}
    names = {r['id']: (r['slug'], r['name']) for r in net.store.all('SELECT id,slug,name FROM projects')}
    listed = sorted(close.values(), key=lambda r: -r['percent'])[:CLOSE_LISTED]
    donors = {}
    for item in listed:
        key = (item['donor_project'], item['donor_function'])
        if key not in donors:
            fn = (net.project_store(key[0]).one('SELECT name FROM functions WHERE id=?', key[1])
                  if key[0] in names else None)
            donors[key] = fn['name'] if fn else 'Removed function'
        slug, game = names.get(item['donor_project'], ('', 'Removed project'))
        item.update(donor=donors[key], donor_slug=slug, game=game)
        del item['donor_project'], item['donor_function']
    out = []
    for step in steps.values():
        tried = len(step['functions'])
        out.append({'step': step['step'], 'label': step['label'], 'checks': step['checks'], 'functions': tried,
                    'matched': len(step['matched']), 'rate': round(100 * len(step['matched']) / tried, 1) if tried else None,
                    'close': len(step['close']), 'unbuilt': step['unbuilt']})
    return {'steps': sorted(out, key=lambda r: -r['matched']), 'close': listed}


def destination_supported(net, proj):
    # Project mode still needs object-relocation alignment and per-file source adaptation.
    slug = proj['slug']
    return not net.fork_slug(slug) and net.is_rom(proj) and proj['arch'] == 'mips' and slug not in net.links


def donor_projects(net, proj):
    from .shared_reuse import compatible
    return {p['id']: p for p in [*compatible(net, proj), proj] if net.is_rom(p)}


AFTER_MATCH_LOOKALIKES = 12  # a new match's closest look-alikes read, of which the open ones are tried


def lookalikes(net, proj, fid) -> list[int]:
    """The open functions whose instructions are close to the matched function `fid`'s."""
    store = net.project_store(proj['id'])
    fn = store.one("SELECT * FROM functions WHERE id=? AND status='matched'", fid)
    if fn is None or not fn['target'] or not MIN_BYTES <= len(fn['target']) <= MAX_BYTES:
        return []
    out = []
    for row, score in net.similar(proj, fn, top=AFTER_MATCH_LOOKALIKES):
        if row['status'] == 'open' and score >= MIN_SIMILARITY:
            found = store.one("SELECT id FROM functions WHERE project_id=? AND name=? AND vram=? AND status='open'",
                              proj['id'], row['name'], row['vram'])
            if found:
                out.append(found['id'])
    return out


FAN_OUT_PER_GAME = 12  # open look-alikes of a new match tried in each other active game


def lookalikes_elsewhere(net, proj, other, fids) -> list[int]:
    """The open functions of `other` (another game) whose code is close to the matched functions `fids` of
    `proj`: read by size from `other`'s rows, shortlisted by shared shingles, kept at MIN_SIMILARITY."""
    store, theirs = net.project_store(proj['id']), net.project_store(other['id'])
    big = bool(proj['big_endian'])
    found = {}
    for fid in fids:
        fn = store.one("SELECT id,target,size FROM main.functions WHERE id=? AND status='matched'", fid)
        if fn is None or not fn['target'] or not MIN_BYTES <= len(fn['target']) <= MAX_BYTES:
            continue
        shape = similarity.mips_shape(fn['target'], big)
        index = similarity.ShingleIndex()
        for row in theirs.all("SELECT id,target FROM main.functions WHERE project_id=? AND status='open' "
                              "AND size BETWEEN ? AND ?", other['id'], int(fn['size'] * .9), int(fn['size'] / .9) + 1):
            if row['target']:
                index.add(row['id'], similarity.mips_shape(row['target'], big))
        for key in index.query(shape, 6):
            alike = similarity.score(list(shape), list(index.shapes[key]))
            if alike >= MIN_SIMILARITY:
                found[key] = max(found.get(key, 0), alike)
    return [key for key, _ in sorted(found.items(), key=lambda item: -item[1])[:FAN_OUT_PER_GAME]]


def after_match(twins, proj, fids) -> int:
    """Functions just matched: their open look-alikes get their C now, with their profile and the literal search,
    rather than at the next autopilot start (one N64 game's agents matched a dozen look-alikes by hand within the
    hour after their donors matched, while the pass waited). Bounded like a pass; misses are cached as usual.

    The look-alikes in the user's other games too: 92% of the close donors of open N64 functions were in another
    game. Only games in active use are opened here (project_activity); an idle game's next pass is best-first by
    donor likeness (`run`), so the new match is near the front of its queue then."""
    from .project_activity import active
    from .shared_reuse import compatible
    from .twins import Job
    net = twins.net
    proj = net.project_row(proj['slug'], sync=False)
    matched = 0
    ids = sorted({b for fid in fids for b in lookalikes(net, proj, fid)}) if destination_supported(net, proj) else []
    if net.is_rom(proj):
        twins.index_project(proj)  # the new donors, before the shortlist reads the index
    if ids:
        matched += run(twins, proj['slug'], Job(), function_ids=ids)
    if not net.is_rom(proj) or proj['arch'] != 'mips':
        return matched
    for other in compatible(net, proj, active_only=True):
        if not net.is_rom(other) or not destination_supported(net, other) or not active(net, other['slug']):
            continue
        found = lookalikes_elsewhere(net, proj, other, fids)
        if found:
            matched += run(twins, other['slug'], Job(), function_ids=found)
    # An idle game isn't opened now (project_activity): the new donors wait for its next pass, saved in the catalog.
    enqueue(net, proj, fids, lambda slug: active(net, slug))
    return matched


FAN_OUT_QUEUE = ('CREATE TABLE IF NOT EXISTS similar_fanout(project_id INTEGER NOT NULL, donor_project_id INTEGER NOT NULL, '
                 'donor_function_id INTEGER NOT NULL, created_at REAL NOT NULL, '
                 'PRIMARY KEY(project_id, donor_project_id, donor_function_id))')
FAN_OUT_BATCH = 200  # queued donors read per pass, oldest first


def enqueue(net, proj, fids, is_active) -> int:
    """Save new matches for each idle game of the same machine to try on its look-alikes at its next pass. Only the
    catalog is written: the idle game's own database stays closed. Whether it really is the same machine and how alike
    its functions are is read when it next runs (`queued`)."""
    rows = [(other['id'], proj['id'], fid, time.time())
            for other in net.store.all('SELECT id,slug FROM projects WHERE id!=? AND arch=? AND big_endian=?',
                                       proj['id'], proj['arch'], proj['big_endian'])
            if not is_active(other['slug']) for fid in fids]
    if not rows:
        return 0
    with net.store.tx() as db:
        db.execute(FAN_OUT_QUEUE)
        db.executemany('INSERT OR IGNORE INTO similar_fanout VALUES(?,?,?,?)', rows)
    return len(rows)


def queued(net, proj, others) -> dict[int, float]:
    """{open function: priority} for this game's look-alikes of the matches saved for it while it was idle; the
    queue's rows read are removed (a donor no longer matched or of another machine is dropped with them)."""
    with net.store.tx() as db:
        db.execute(FAN_OUT_QUEUE)
        rows = db.execute('SELECT donor_project_id,donor_function_id FROM similar_fanout WHERE project_id=? '
                          'ORDER BY created_at LIMIT ?', (proj['id'], FAN_OUT_BATCH)).fetchall()
        db.executemany('DELETE FROM similar_fanout WHERE project_id=? AND donor_project_id=? AND donor_function_id=?',
                       [(proj['id'], r[0], r[1]) for r in rows])
    by_donor: dict[int, list[int]] = {}
    for donor_project, fid in rows:
        if donor_project in others:
            by_donor.setdefault(donor_project, []).append(fid)
    out = {}
    for donor_project, fids in by_donor.items():
        for fid in lookalikes_elsewhere(net, others[donor_project], proj, fids):
            out[fid] = 1.5
    return out


def byte_exact_leads(net, proj, others) -> dict[int, list[int]]:
    """{open function: donors whose C matched it byte for byte under the donor's own profile}: what the pass
    recorded as a "review compiler settings" lead before such results could become matches. Each is tried first
    with the donor's profile through lookalike_profiles.trial, which publishes that profile for the function alone
    on an exact recheck."""
    out: dict[int, list[int]] = {}
    for row in net.project_store(proj['id']).all(
            "SELECT DISTINCT c.function_id,c.source_function_id,c.source_project_id FROM cross_game_attempts c "
            "JOIN main.functions f ON f.id=c.function_id AND f.status='open' "
            "WHERE c.project_id=? AND c.outcome='compiler'", proj['id']):
        if row['source_project_id'] in others:
            out.setdefault(row['function_id'], []).append(row['source_function_id'])
    return out


PRIORITY_SHARE = .25  # of a pass's remaining time, at most, spent ordering its functions
_PRIORITIES: dict = {}


def best_first(net, proj, donors, targets, leads, deadline) -> dict[int, float]:
    """{function: how alike its most similar donor is (shared shingles, 0..1)}; byte-exact leads above all.
    Cached per function for as long as the donor library has the same size, so only the first pass after a
    change pays for it; when ordering would take more than PRIORITY_SHARE of the pass, the rest keep id order."""
    out = {fid: 2.0 for fid in leads}
    wanted = [row['id'] for row in targets if row['id'] not in out]
    if not wanted or not donors.rows:
        return out
    # Keyed by the code itself (a resplit function is another one) and the library's size (new donors).
    stamp = len(donors.rows)
    stop = time.monotonic() + max(0.0, deadline - time.monotonic()) * PRIORITY_SHARE
    index, big = None, bool(proj['big_endian'])
    store = net.project_store(proj['id'])
    for start in range(0, len(wanted), 2000):
        for row in store.all('SELECT id,target FROM main.functions WHERE id IN (SELECT value FROM json_each(?)) '
                             'AND size BETWEEN ? AND ?', json.dumps(wanted[start:start + 2000]), MIN_BYTES, MAX_BYTES):
            key = (stamp, row['target'] or b'')
            if key not in _PRIORITIES:
                if time.monotonic() >= stop:
                    continue
                index = index or donors.index()
                _PRIORITIES[key] = index.estimate(similarity.mips_shape(key[1], big))
            out[row['id']] = _PRIORITIES[key]
    if len(_PRIORITIES) > 400_000:
        _PRIORITIES.clear()
    return out


def run(twins, slug: str, job, *, function_ids=None) -> int:
    """Try a few close candidates per function after exact transfers, with a pass-wide time/compile budget."""
    from .twins import _declared, apply, rom_renames

    net = twins.net
    proj = net.project_row(slug)
    if not destination_supported(net, proj):
        return 0
    cfg = net.cfg(proj)
    manual = getattr(job, 'cross_exhaustive', False)
    supported_compilers = {c.id for c in net.console(proj).compilers_for(cfg)}
    others = donor_projects(net, proj)
    from . import free_candidates
    free_candidates.budget(job, MAX_SECONDS, MAX_ATTEMPTS)
    deadline = job.reuse_deadline
    if function_ids is None:  # The coordinator refreshes the indexes before launching separate lanes.
        for other in others.values():
            if job.stop.is_set() or time.monotonic() >= deadline:
                return 0
            twins.index_project(other)
    agent, _ = net.local_agent('autopilot')
    mine = twins.prints(proj)
    syms_b = net.symbols(proj['id'])
    symbol_state = [(r['name'], r['address']) for r in net.project_store(proj['id']).all(
        'SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    source_cache, ops_cache = {}, {}
    from . import address_repairs, parameter_transfers, regional_layouts, reuse_donors, twin_fields
    donors = reuse_donors.Donors(net, proj, include_self=True)
    layouts = {pid: regional_layouts.learn(net, proj, other) for pid, other in others.items()}
    maps = {pid: regional_names(twins, proj, other) for pid, other in others.items()}
    cursor = 0
    # A run over chosen functions (a lane, or the look-alikes of a new match) leaves the pass's place alone.
    keeps_place = not manual and function_ids is None
    if keeps_place:
        saved = net.project_store(proj['id']).one('SELECT function_id FROM reuse_cursors WHERE project_id=?', proj['id'])
        cursor = saved['function_id'] if saved else 0

    def resume(fid):
        if keeps_place:
            with net.project_store(proj['id']).tx() as db:
                db.execute('INSERT OR REPLACE INTO reuse_cursors VALUES(?,?)', (proj['id'], fid))
    attempted = matched = 0
    repairs = {}

    def exhausted():
        return job.stop.is_set() or job.reuse_attempts >= job.reuse_limit or time.monotonic() >= deadline

    feedback = {}

    def attempt(a, b, other, score, candidate, settings, kind, names, header, first=None):
        nonlocal attempted, matched
        feedback.clear()
        current = net.project_store(proj['id']).one('SELECT status FROM functions WHERE id=?', b['id'])
        if not current or current['status'] != 'open':
            return 'skip', ''
        from decomprepair.repair_preflight import invalid_call
        if invalid_call(candidate, b['name'], header):
            # A callee that gained or lost a parameter between the two games: the donor's calls given the counts
            # this game's prototypes declare (decomprepair.call_arity), rather than the donor never being tried.
            from decomprepair.call_arity import fit
            fitted = fit(candidate, b['name'], header)
            if not fitted or invalid_call(fitted, b['name'], header):
                return 'skip', 'Generated call disagrees with a fixed prototype'
            candidate = fitted
        # (the compile-guided trials are keyed apart: a lead or a miss cached for the same C and profile was
        # judged without the literal search, and never with the profile applied)
        signature = trial_key(candidate, header, settings, b, [names, symbol_state] + ([kind] if kind in SEARCHED else []))
        previous = net.project_store(proj['id']).one("SELECT * FROM reuse_trials WHERE function_id=? AND signature=? AND outcome!='match'",
                                 b['id'], signature)
        if previous:
            feedback.update(json.loads(previous['feedback']))
            return previous['outcome'], previous['reason']
        if exhausted():
            return 'skip', ''
        job.cross_candidates += 1
        job.cross_tried += 1
        attempted += 1
        job.reuse_attempts += 1
        if kind in SEARCHED:
            from . import lookalike_profiles
            outcome, why, compiles = lookalike_profiles.trial(
                net, agent, proj, b, _noted(candidate, a, other, proj), None if kind == 'literals' else settings,
                first=first, budget=max(0, min(lookalike_profiles.SEARCH_COMPILES, job.reuse_limit - job.reuse_attempts)))
            job.reuse_attempts += max(0, compiles - 1)
        else:
            outcome, why = twins._attempt_twin(agent, proj, a, b, candidate,
                origin=f" in {other['name']}", similar=True, feedback=feedback, mapping=names)
        donor_hash = hashlib.sha256(a['matched_source'].encode()).hexdigest()
        reason = f'similar code ({score:.0%}); {kind}; donor source {donor_hash}; ' + (why or outcome)
        with net.project_store(proj['id']).tx() as db:
            db.execute('INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,'
                       'source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)',
                       (proj['id'], b['id'], other['id'], a['id'], outcome, reason, time.time()))
            if outcome != 'skip':
                db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                           (b['id'], signature, outcome, why or '', json.dumps(feedback)))
        if outcome == 'match':
            matched += 1
            job.reuse_matched += 1
            job.reuse_bytes += b['size']
            job.cross_matched += 1
            maps[other['id']] = regional_names(twins, proj, other)
        return outcome, why

    # Keep a quarter of the remaining budget for close misses. Previously a large transfer queue could
    # consume every pass, leaving hundreds of eligible cached misses with no repair attempts at all.
    transfer_deadline = time.monotonic() + max(0, deadline - time.monotonic()) * .75
    transfer_limit = (job.reuse_attempts + max(1, int((job.reuse_limit - job.reuse_attempts) * .75))
                      if job.reuse_limit != float('inf') else job.reuse_limit)
    incomplete = False

    def transfers_exhausted():
        return bool(repairs) and (job.reuse_attempts >= transfer_limit or time.monotonic() >= transfer_deadline)

    store = net.project_store(proj['id'])
    # Most open functions have no usable fingerprint or are already claimed.
    # Do not inflate all their source payloads before testing those cheap gates.
    selected = ' AND id IN (SELECT value FROM json_each(?))' if function_ids is not None else ''
    params = (json.dumps(function_ids),) if function_ids is not None else ()
    targets = metadata_rows(store, ('id', 'name'),
                            "SELECT id,name FROM main.functions WHERE project_id=? AND status='open'" + selected
                            + " ORDER BY id", proj['id'], *params)
    leads = byte_exact_leads(net, proj, others)
    priority = best_first(net, proj, donors, targets, leads, deadline)
    if function_ids is None:  # a whole pass: what matched elsewhere while this game was idle goes after the leads
        for fid, value in queued(net, proj, others).items():
            priority[fid] = max(priority.get(fid, 0.0), value)
    # Manual searches widen each target before moving on. An entire project of
    # cheap misses should not use up the budget before the wider search starts.
    # Within that, the functions with the most alike donors go first: the pass used to walk them by id, so on a
    # large game it spent its budget on functions with no close donor and reached few that had one.
    work = [(phase, b) for phase in (0, 1) for b in targets]
    def rank(item):
        phase, row = item
        first = -priority.get(row['id'], 0.0)
        return (first, row['id'], phase) if manual else (phase, first, row['id'])
    work.sort(key=rank)
    # Resume at the saved place in that order (a function and phase), wrapping round.
    here = next((i for i, (phase, row) in enumerate(work)
                 if row['id'] == abs(cursor) and phase == int(cursor < 0)), 0) if cursor else 0
    work = work[here:] + work[:here]
    from .cross_game_progress import report as publish_progress
    for index, (phase, b) in enumerate(work):
        publish_progress(job, 'Checking similar matched functions', function=b['name'], done=index, total=len(work))
        if job.stop.is_set() or job.reuse_attempts >= job.reuse_limit or time.monotonic() >= deadline:
            return matched
        if transfers_exhausted():
            incomplete = True
            break
        resume(-b['id'] if phase else b['id'])
        pb = mine.get(b['id'])
        if pb is None or pb.asm or not MIN_BYTES <= pb.size <= MAX_BYTES:
            continue
        if store.one('SELECT 1 FROM claims WHERE function_id=?', b['id']):
            continue
        b = store.one("SELECT * FROM functions WHERE project_id=? AND id=? AND status='open'", proj['id'], b['id'])
        if b is None:
            continue  # matched/deleted since the metadata snapshot or the preceding phase
        own = compiler_for(cfg, b['vram'], b)
        own_key = twins._compiler_key(own)
        own_toolchain = own.get('toolchain')
        wide = manual and phase == 1
        ratio = MANUAL_SIZE_RATIO if wide else .9
        floor = MANUAL_SIMILARITY if wide else MIN_SIMILARITY
        shortlist = (pb.size, max(MIN_BYTES, int(pb.size * ratio)), min(MAX_BYTES, int(pb.size / ratio)),
                     pb.key, own_key)
        own_codegen = codegen_key(own_key)
        rows = donors.candidates(*shortlist, MAX_POOL, near=b['vram'], code=b['target'],
                                 eligible=lambda row, exact=phase == 0, key=own_codegen, tc=own_toolchain: (
            row['project_id'] in others and (codegen_key(row['compiler']) == key if exact else (
                _toolchain(row['compiler']) == tc or row['project_id'] == proj['id']))))
        # A donor whose C already matched this function byte for byte under its own profile goes first.
        pinned = [row for row in (donors.row(fid) for fid in leads.get(b['id'], ())) if row is not None]
        rows = pinned + [row for row in rows if row['id'] not in {p['id'] for p in pinned}]
        # Keep an independent shortlist for other compiler builds. Rows from a
        # large same-compiler library would otherwise fill the size-based cap.
        alternate = donors.candidates(*shortlist, OTHER_COMPILER_POOL, code=b['target'], eligible=lambda row, tc=own_toolchain: (
            row['project_id'] in others and _toolchain(row['compiler']) != tc)) if wide else []
        layout = [(o, t) for o, t, _ in relocations(b)]
        lead_ids = set(leads.get(b['id'], ()))
        def eligible(a, layout=layout, wide=wide, own_codegen=own_codegen, phase=phase, lead_ids=lead_ids):
            if not a['has_matched']:
                return False
            if wide or a['id'] in lead_ids:
                return wide or phase == 0
            same_layout = [(o, t) for o, t, _ in relocations(a)] == layout
            # Another layout is renamed from the aligned runs of instructions (_aligned_prints) below.
            return (not same_layout or codegen_key(a['compiler']) != own_codegen) == bool(phase)

        rows = [a for a in rows if eligible(a)]
        alternate = [a for a in alternate if eligible(a)]
        if not rows and not alternate:
            continue
        ops = _mnemonics(net, proj, b)
        by_id = {str(a['id']): a for a in [*rows, *alternate]}
        for a in by_id.values():
            if a['id'] not in ops_cache:
                ops_cache[a['id']] = _mnemonics(net, others[a['project_id']], a)

        big = bool(proj['big_endian'])
        ranked = _rank_donors(rows, ops, ops_cache, b, layout, big, floor)
        extra = _rank_donors(alternate, ops, ops_cache, b, layout, big, floor)[:MANUAL_OTHER_COMPILERS] if alternate else []
        ranked = extra + ranked[:(MANUAL_CANDIDATES if wide else MAX_CANDIDATES) - len(extra)]
        # A byte-exact lead's donor is tried whatever the ranking's cap.
        firsts = [(str(a['id']), 1.0) for a in rows if a['id'] in lead_ids]
        ranked = firsts + [item for item in ranked if item[0] not in {key for key, _ in firsts}]
        renamed = []  # each donor's C with this function's names, in ranked order (the consensus below)
        for key, score in ranked:
            a = by_id[key]
            # Re-read authoritative source after ranking: donors can be polished during a pass.
            fresh = net.project_store(a['project_id']).one(
                "SELECT * FROM functions WHERE id=? AND status='matched'", a['id'])
            if fresh is None or not fresh['matched_source'] or fresh['target'] != a['target']:
                continue
            a = {**a, **dict(fresh)}
            other = others[a['project_id']]
            if other['id'] not in source_cache:
                source_cache[other['id']] = (twins.prints(other), net.symbols(other['id']),
                                              net._context(other['id'])['header'])
            prints, syms_a, header_a = source_cache[other['id']]
            pa = prints.get(a['id'])
            if pa is None:
                continue
            learned = maps[other['id']]
            # In its own game an address is the same thing on both sides: only another game's names are looked up
            theirs = syms_b if other['id'] != proj['id'] else None
            aligned = [(o, t) for o, t, _ in relocations(a)] == layout
            if not aligned:
                left, right = _aligned_prints(a, b, pa, pb, ops_cache[a['id']], ops)
                # Outside a manual search every reference of the target has to be accounted for by an aligned
                # one of the donor's (or by names learned from accepted transfers): a guess costs a compile.
                if not wide and len(right.addrs) < len(layout) and not learned:
                    continue
                local = rom_renames(syms_a, a['matched_source'], left, right, a['name'], b['name'], theirs)
                # Keep established mappings when a shifted instruction offers conflicting evidence.
                if local is not None:
                    local = {**local, **learned}
            else:
                left, right = pa, pb
                local = rom_renames(syms_a, a['matched_source'], pa, pb, a['name'], b['name'], theirs) if aligned else {}
            names = None if local is None else {**learned, **local, a['name']: b['name']}
            if names is None:
                continue
            words = set(re.findall(r'\b[A-Za-z_]\w*\b', a['matched_source']))
            names = {old: new for old, new in names.items() if old in words}
            header_b = net._context(proj['id'], b['unit'])['header']
            prelude = _declared(header_a, header_b, names, a['name'])
            source = apply(a['matched_source'], names)
            renamed.append(source)
            parameter_sources = parameter_transfers.variants(source, a, b, bool(proj['big_endian']))
            destination = b['best_source'] or b['seed'] or ''
            parameter_sources = list(dict.fromkeys([parameter_transfers.destination_signature(v, b['name'], destination)
                                                    for v in parameter_sources] + parameter_sources))
            donor = json.loads(a['compiler'])
            literals = literal_variants(source, a, b, big)
            # In its own game only: another field of one table, or a literal where the donor loads a global
            # (app/twin_fields.py)
            fields = [prelude + v for v in twin_fields.variants(a['matched_source'], names, list(zip(left.addrs, right.addrs)),
                                                                a, b, syms_a, apply, big)] if theirs is None else []
            transfers = [(v, own, 'transfer') for v in
                         [source, *literals, *regional_layouts.variants(source, a, b, layouts[other['id']], big)]]
            variants = [(v, own, 'parameter transfer') for v in parameter_sources] + transfers[:1]
            donor_toolchain = donor.get('toolchain')
            same_game = other['id'] == proj['id']
            # A look-alike in its own game is built with its donor's profile as often as with the default (one N64
            # game: 326 functions at -O1 beside an -O2 default, and SN builds): the donor's C is tried with the
            # donor's profile and the compile-guided literal search, and an exact match makes the profile this
            # function's own (lookalike_profiles.py). The function's own profile keeps the first try.
            # Another game's look-alike is tried the same way: its compiler profile became a "review compiler
            # settings" note before, never a match (592 byte-exact results, 167 of their functions still open).
            if (donor_toolchain and codegen_key(donor) != own_codegen and not donor.get('cmd') and not own.get('cmd')
                    and (same_game or donor_toolchain in supported_compilers) and toolchains.installed(donor_toolchain)):
                variants.append((source, donor, 'profile'))
                variants += [(v, donor, 'profile') for v in fields[:1]]
            variants += [(v, own, 'transfer') for v in fields[:2]]
            variants += transfers[1:]
            variants.append((source, own, 'literals'))
            plain = None
            for position, (variant, settings, kind) in enumerate(variants):
                if exhausted():
                    return matched
                if transfers_exhausted():
                    incomplete = True
                    break
                first = None
                if kind == 'literals':
                    # Only a plain transfer that built the target's shape, wrong in constants alone, is searched:
                    # its compiled body (fresh or cached) is the search's start, so nothing is compiled twice.
                    first = _measured(plain, b)
                    if first is None or first.exact or not mips_immediates.value_pairs(
                            first.code, b['target'] or b'', first.relocated, big):
                        continue
                candidate = prelude + variant
                outcome, why = attempt(a, b, other, score, candidate, settings, kind, names, header_b, first=first)
                if kind == 'transfer' and variant == source:
                    plain = dict(feedback)
                    # 606 transfers didn't build, mostly for a type or declaration only the donor's header has:
                    # its missing items, in this game's names, are tried once before the variants that follow.
                    if outcome == 'miss' and plain.get('score') is None and (
                            types := donor_types(header_a, header_b, names, syms_b)):
                        variants.insert(position + 1, (types + variant, settings, 'typed transfer'))
                # Built in the target's shape but off in where struct fields are: the other game's struct has
                # them elsewhere (a field inserted before them), and no number in the C says it. The donor's struct
                # laid out again, or the uses written at the target's offsets (decomprepair.field_offsets).
                if kind in TRANSFERS and outcome == 'miss' and kind != 'fields':
                    for moved in field_variants(variant, header_b, feedback, b, big)[:2]:
                        variants.insert(position + 1, (moved, settings, 'fields'))
                # Several games' C for this one helper, alike apart from some numbers: those numbers are what each
                # game changes, so they are the ones set from this game's instructions (`consensus`).
                if kind == 'transfer' and variant == source and outcome == 'miss' and len(renamed) > 1:
                    agreed = consensus(source, renamed[:-1], feedback, b, big)
                    if agreed:
                        variants.insert(position + 1, (agreed, settings, 'consensus'))
                # Easy transfers get the first pass through the budget. Then repair the closest compiled misses.
                close = re.fullmatch(r'score (\d+)', why or '')
                percent = feedback.get('percent', 100 * (1 - int(close[1]) / max(1, (b['n_insns'] or 0) * 100)) if close else 0)
                if kind in TRANSFERS and outcome == 'miss' and close and percent >= REPAIR_PERCENT:
                    pair = (a['id'], b['id'])
                    if pair not in repairs or int(close[1]) < repairs[pair][0]:
                        repairs[pair] = (int(close[1]), a, b, other, score, prelude, variant, settings, names, header_b, dict(feedback))
                if outcome == 'match':
                    break
            if incomplete:
                break
            if net.project_store(proj['id']).one('SELECT status FROM functions WHERE id=?', b['id'])['status'] == 'matched':
                break
        if incomplete:
            break
    # Cached trials regenerate this deterministic queue, so a later pass resumes even after an app restart.
    # Concrete address evidence goes before speculative cast/order mutations within the repair allowance.
    known = {name for name, _ in symbol_state}
    prepared = []
    for r in repairs.values():
        if exhausted():
            return matched
        prepared.append((address_repairs.variants(r[5] + r[6], r[10], r[2], bool(proj['big_endian']), known), r))
    prepared.sort(key=lambda item: (not item[0], item[1][0]))
    repair_limit = MANUAL_REPAIRS if manual else MAX_REPAIRS
    for address_variants, (_, a, b, other, score, prelude, source, settings, names, header, report) in prepared:
        publish_progress(job, 'Repairing similar donor code', function=b['name'])
        if exhausted():
            if not incomplete:
                resume(0)
            return matched
        if net.project_store(proj['id']).one('SELECT status FROM functions WHERE id=?', b['id'])['status'] != 'open':
            continue
        guided = list(address_variants)
        if report.get('candidate_target'):
            compiled = {'target': bytes.fromhex(report['candidate_target']), 'relocs': report.get('candidate_relocs', '[]')}
            guided.extend(literal_variants(prelude + source, compiled, b, bool(proj['big_endian'])))
        simple = repair_variants(source, report)
        deep = deeper_variants(source, header + '\n' + prelude, settings, b['name'], deadline, repair_limit // 2)
        variants = [*guided, *(prelude + v for v in (simple[:repair_limit // 2] if deep else simple)),
                    *(prelude + v for v in deep)]
        for variant in list(dict.fromkeys(variants))[:repair_limit]:
            if exhausted():
                if not incomplete:
                    resume(0)
                return matched
            outcome, _ = attempt(a, b, other, score, variant, settings, 'repair', names, header)
            if outcome == 'match':
                break
    if not incomplete:
        resume(0)
    return matched
