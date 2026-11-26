"""A matched look-alike's C tried with the donor's own compiler profile and a compile-guided literal search.

A game's look-alikes are often one source compiled again with other constants, and a game with per-function
compiler profiles (one N64 game: 360 of them, mostly `-O1` beside a `-O2` default, some SN builds) builds a
look-alike with its donor's profile. The similar-code pass (`similar_matches`) renamed the donor's symbols and
judged the result with the target's own profile only; the donor's profile was a "lead" for a person to review, and
constants were substituted only when one singleton literal matched one changed instruction. Matching agents then
matched those functions by hand on the first try, copying the donor and changing two numbers.

Here a trial compiles the candidate with the given profile, scoped to this one function. When the compiled body has
the target's shape and differs only in immediates, it reads which constants disagree
(`arch.mips_immediates.value_pairs`: signed values, LUI/ORI 32-bit constants, repeated and swapped values) and tries
each C literal with that value, keeping an edit only when the compiler confirms fewer differing instructions. An
exact result is judged again through the ordinary recorder (`try_quietly`, exact-only), and only that publishes the
profile, for this function alone (`exact_match_inputs.publish`): never a range or the project's default, never on
an inexact score.

ROM mode, MIPS (the similar-code pass's reach); project mode has no per-function profiles to carry (ROADMAP).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .. import scoring, toolchains
from ..arch import mips_immediates
from ..network import NetworkError

SEARCH_COMPILES = 40   # compiles one trial's literal search may spend, beside the first
TOKENS = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
                    r'[A-Za-z_]\w*|(?<![\w.])(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:[uUlL]*)(?![\w.])', re.S)
INTEGER = re.compile(r'(0[xX][0-9a-fA-F]+|[0-9]+)([uUlL]*)\Z')
MASK = 0xffffffff


@dataclass
class Measure:
    exact: bool
    score: int | None
    code: bytes
    relocated: set
    differing: int  # aligned non-relocated words that differ; a large number when the lengths differ


def differing(code: bytes, target: bytes, relocated: set) -> int:
    if len(code) != len(target):
        return 1 << 20
    return sum(code[o:o + 4] != target[o:o + 4] for o in range(0, len(code), 4) if o not in relocated)


def private(proj, fn, settings):
    """The project as this one function sees it built with `settings` (None: as it is)."""
    if settings is None:
        return proj
    from .prepared_evidence import scoped_config
    return {**dict(proj), 'config': json.dumps(scoped_config(proj, fn, settings))}


def measure(net, proj, fn, source: str, settings=None) -> Measure | None:
    """Compiled and linked like an attempt, recording nothing. None when it doesn't build."""
    trial = private(proj, fn, settings)
    header = net._context(proj['id'], fn['unit'])['header']
    result = net._compile(trial, header, source, fn)
    if not result.ok:
        return None
    candidate, verdict, _ = net._evaluate(fn, trial, result.obj)
    if candidate is None:
        return None
    diff = scoring.diff(net._target_obj(fn, trial), candidate)
    relocated = {r.offset for r in candidate.relocs} | {int(r[0]) for r in json.loads(fn['relocs'] or '[]')}
    exact = verdict if verdict is not None else diff.bytes_match
    return Measure(bool(exact), diff.score, candidate.code, relocated,
                   0 if exact else differing(candidate.code, fn['target'] or b'', relocated))


def _value(text: str) -> int:
    return int(text, 16 if text.lower().startswith('0x') else 10)


def _format(old: str, value: int) -> str:
    if old.lower().startswith('0x'):
        digits = f'{value:X}' if any(c.isupper() for c in old[2:]) or old[2:].isdigit() else f'{value:x}'
        return old[:2] + digits
    return str(value)


def literals(source: str) -> list[tuple[int, int, str, str, bool, int]]:
    """[(start, end, digits, suffix, negated, value as built)] for each C integer literal outside comments and
    strings. A literal after a minus reads as negative (`x - 2` is `addiu -2`)."""
    out = []
    for token in TOKENS.finditer(source):
        m = INTEGER.fullmatch(token.group())
        if not m or _value(m[1]) > MASK:
            continue
        negated = source[:token.start()].rstrip().endswith('-')
        out.append((token.start(), token.end(), m[1], m[2], negated, -_value(m[1]) if negated else _value(m[1])))
    return out


def _written(literal, new) -> str | None:
    """The literal's text for the built value `new`, or None when only another operator could say it (a change of
    sign: `x - 2` can't become `x + 1` by its number)."""
    _, _, digits, suffix, negated, _ = literal
    signed = new - (1 << 32) if new > 0x7fffffff else new
    if negated:
        wanted = -signed if signed < 0 else None
    elif new < 0:
        wanted = new & MASK if _value(digits) > 0x7fffffff else None  # (-1 spelled 0xFFFFFFFF stays so)
    else:
        wanted = new
    return None if wanted is None else _format(digits, wanted) + suffix


def proposals(source: str, pairs) -> list[tuple[int, int, str]]:
    """[(start, end, text)]: each C integer literal whose value the compiler built as one of `pairs`' compiled
    values, rewritten to the wanted value."""
    out = []
    for literal in literals(source):
        for old, new in pairs:
            if literal[5] & MASK == old & MASK and (text := _written(literal, new)) is not None:
                out.append((literal[0], literal[1], text))
    return list(dict.fromkeys(out))


MARKERS = 24  # literals one marked build tells apart
MARK_RANGE = range(3, 120)  # marker magnitudes: small enough for a char-typed sum (an s8 `tmp + 275` built as 19)


def _replace(source, edits):
    for start, end, text in sorted(edits, reverse=True):
        source = source[:start] + text + source[end:]
    return source


def locate(net, proj, fn, source, settings, current):
    """(source, compiles): every small literal whose value the compiled body disagrees on, set at once to what the
    target has where that literal was built. Which instruction a literal becomes is read from one build with each
    such literal moved to a value of its own: one look-alike's `+1`/`+2` are another's `+2`/`+1`, and changing one
    at a time made two branches identical, which the compiler then merged, so no single edit got closer."""
    big = bool(proj['big_endian'])
    target = fn['target'] or b''
    olds = {old & MASK for old, new in mips_immediates.value_pairs(current.code, target, current.relocated, big)
            if -0x8000 <= old < 0x8000}
    marked = [lit for lit in literals(source) if lit[5] & MASK in olds and lit[5]][:MARKERS]
    if not marked:
        return source, 0
    # Values the body already holds anywhere, or the C writes, can't tell a literal apart.
    order = 'big' if big else 'little'
    taken = {lit[5] for lit in literals(source)}
    for at in range(0, len(current.code), 4):
        half = int.from_bytes(current.code[at:at + 4], order) & 0xffff
        taken |= {half, half - 0x10000}
    free = [m for m in MARK_RANGE if m not in taken and -m not in taken]
    if len(free) < len(marked):
        return source, 0
    markers = {}
    edits = []
    for literal, magnitude in zip(marked, free):
        value = magnitude if literal[5] > 0 else -magnitude
        text = _written(literal, value & MASK if value < 0 else value)
        if text is None:
            return source, 0
        markers[value] = literal
        edits.append((literal[0], literal[1], text))
    probe = measure(net, proj, fn, _replace(source, edits), settings)
    if probe is None:
        return source, 1
    wanted = {}
    for _, built, goal in mips_immediates.value_sites(probe.code, target, probe.relocated, big):
        if built in markers:
            literal = markers[built]
            if wanted.setdefault(literal[:2], goal) != goal:
                return source, 1  # one literal built into two places that want different values
    edits = []
    for literal in marked:
        goal = wanted.get(literal[:2], literal[5])
        if goal != literal[5]:
            text = _written(literal, goal)
            if text is None:
                return source, 1
            edits.append((literal[0], literal[1], text))
    return (_replace(source, edits) if edits else source), 1


def search(net, proj, fn, source: str, settings, first: Measure, budget: int):
    """(source, measure, compiles): the literals located at once (`locate`), then greedy single edits, each kept
    only when it leaves fewer differing instructions. Stops at an exact result, at no confirmed improvement or
    after `budget` compiles."""
    big = bool(proj['big_endian'])
    current, compiles, rejected = first, 0, set()
    if not current.exact and budget >= 2:
        located, spent = locate(net, proj, fn, source, settings, current)
        compiles += spent
        if located != source:
            compiles += 1
            result = measure(net, proj, fn, located, settings)
            if result is not None and (result.exact or result.differing < current.differing):
                source, current = located, result
    while not current.exact and compiles < budget:
        pairs = mips_immediates.value_pairs(current.code, fn['target'] or b'', current.relocated, big)
        if not pairs:
            break
        improved = False
        for start, end, text in proposals(source, pairs):
            key = (source[:start].count('\n'), len(source[:start]) - source[:start].rfind('\n'), text)
            if key in rejected:
                continue
            if compiles >= budget:
                break
            candidate = source[:start] + text + source[end:]
            compiles += 1
            result = measure(net, proj, fn, candidate, settings)
            if result is not None and (result.exact or result.differing < current.differing):
                source, current, improved = candidate, result, True
                break
            rejected.add(key)
        if not improved:
            break
    return source, current, compiles


def trial(net, agent, proj, fn, source: str, settings=None, *, first: Measure | None = None,
          budget: int = SEARCH_COMPILES) -> tuple[str, str, int]:
    """(match | miss | skip, why, compiles) for `source` on the open function `fn`, built with `settings` (a
    donor's profile; None: its own). Only an exact result recorded through the ordinary judge counts; with a
    donor's profile that also makes the profile this function's own."""
    from ..project import compiler_for
    proj = net.project_row(proj['slug'], sync=False)
    store = net.project_store(proj['id'])
    current = store.one('SELECT * FROM functions WHERE id=?', fn['id'])
    if current is None or current['status'] != 'open' or store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id']):
        return 'skip', 'function is busy or no longer open', 0
    own = compiler_for(net.cfg(proj), current['vram'], current)
    if settings is not None and all(settings.get(k) == own.get(k) for k in ('toolchain', 'flags')):
        settings = None
    if settings is not None:
        if current['rom_offset'] is None:
            return 'skip', 'a compiler profile selects a function by its place in the original image', 0
        if settings.get('cmd') or not settings.get('toolchain') or not toolchains.installed(settings['toolchain']):
            return 'skip', 'the donor compiler is not installed', 0
        settings = {'toolchain': settings['toolchain'], 'flags': list(settings.get('flags', []))}
    fn = current
    label = ' with ' + settings['toolchain'] + ' ' + ' '.join(settings.get('flags', [])) if settings else ''
    try:
        compiles = 0
        if first is None:
            first = measure(net, proj, fn, source, settings)
            compiles = 1
        if first is None:
            return 'miss', ('did not build' + label)[:160], compiles
        source, result, searched = search(net, proj, fn, source, settings, first, budget)
        compiles += searched
        if not result.exact:
            return 'miss', f'score {result.score}{label}', compiles
        # The exact recheck records it like any match; with a donor's profile, it publishes that profile for
        # this one function in the same transaction (exact_match_inputs.publish), or nothing at all.
        answer = net.try_quietly(agent, private(proj, fn, settings), fn, source,
                                 synchronize=False, exact_only=True)
    except (NetworkError, toolchains.ToolchainError) as error:
        return 'skip', str(error)[:160], 0
    if answer.get('matched'):
        return 'match', ('byte match' + label)[:160], compiles + 1
    return 'miss', f'exact recheck failed{label}'[:160], compiles + 1
