"""The profile probe: an open function's saved C judged under the few per-function compiler profiles its target points to.

An N64 game's default profile (SN GCC 2.8.1, `-mfp64`) was wrong for about two-thirds of the ~1,000 functions an agent
matched by hand, and the right one was one of a small family each with a tell in the target's own code: another
floating-point register file (`-mfp32`), another small-data threshold (`-G8`, the float pool read through `$at`), an
older compiler (an epilogue `jr ra; nop` that SN GCC 2.8.1 never leaves; SN 2.7.2's builds), or a library's own compiler
(KMC GCC 2.7.2 for libultra). Agents found each by calling `compiler_experiments` function by function, slowly. This step
does it where the permuter and the autopilot already are, with no model:

1. decide (`plan`): from the saved C's diff, the target's bytes, the function's library identity and the profiles its
   matched neighbours were built with, which profiles are plausible. No evidence, no compile: a function with nothing
   pointing anywhere is not tried. `Console.profile_probes` adds the console's own (consoles/n64/profile_probes.py:
   SN 2.7.2 builds and KMC for libultra); the MIPS GCC ones that depend only on flags (`-mfp32`, `-G`, and both) and the
   neighbours' vote are here, and so is `-fforce-addr` for a target that reads offsets off a held symbol address.
   The -O levels need no evidence (opt_levels.py): every function's own flags at each other
   level of its compiler, on any processor.
2. judge (`run`): each profile through deep_profiles.probe, the same call the permuter's profile sweep uses, so a result
   is cached durably per (function, saved C, build inputs, profile) and never compiled twice, and an EXACT pair is
   rechecked and applied for that function only through `suggest`, with the project's own rule for automatic compiler
   changes (a project that doesn't apply them leaves the exact pair on the project page). The machine draft, when it is
   not the saved C, is judged under each profile too: under another profile it is often the closer text. Floating-point,
   small-data and scheduling variants apply like any other profile here: the permuter's sweep leaves them to review, but
   they are what this step is for, and only an exact, rechecked pair is ever applied.
3. say (`diagnose`): the best profile that was not exact is a note on the function ("-mfp32: 20 against 380") for the
   agent that picks it up next. It changes nothing: never a compiler range, never a non-exact profile.
4. search (`closer_level`, profile_climb.explore_level): when a level is clearly closer, the Deep Permuter repairs the C
   under that level. The draft is compared with itself (its score under the level against its score under the
   function's own settings): the saved C was climbed under the own settings, and one that scored 145 there and 450
   under -O1 hid a draft at 250 under -O1 that three repairs made exact.

Cost, once per function, saved C and build inputs, for a GCC function at -O2 (-O0, -O1, -O3, -Os): up to 4 compiles
for the saved C, 4 for the draft and 1 for the draft under its own settings. A verdict the sweep or an earlier pass
kept is not compiled again. The search under a closer level comes out of the pass's own allowance (at most a quarter of
what is left, profile_climb).

Both callers hold the function the way they always do and stop through their own Stop: the Deep Permuter after its
profile sweep (deep_permuter.py) and the autopilot's saved-source pass (profile_probe_autopilot.py, which leaves the
search of step 4 to the Deep Permuter). A Deep Permuter pass that doesn't repeat finished functions still gives them
this probe (profile_probe_autopilot.catch_up). A project nobody is
working on is not touched (project_activity). ROM mode only: a project-mode build gives each source file its compiler
(`Console.file_compiler`) and `try_compiler` switches it; that is ROADMAP.md's "Profile probes for project mode".
The decision rules read code and flags only; they never compile.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from ..arch.mips_profile_evidence import (
    float_pool_through_at,
    keeps_frame_pointer,
    reads_through_held_address,
    saves_before_parameter_moves,
    words_of,
)

MAX_PROBES = 10  # profiles the evidence names, judged per function and saved C (the -O levels come on top)
NEIGHBOUR_MIN = 3  # matched neighbours on one other profile that make it the first profile to judge
NEIGHBOUR_SHARE = 2 / 3
NOTE_AT = 0.9  # a non-exact profile is told to the next agent when it scores at most this share of the saved C's own score
COMMON_MIN = 5  # functions of the project on one per-function profile before it is worth judging under an unrelated one
COMMON_PROBES = 2  # ... at most this many of them, the most used first
RESERVE = 3  # compiles left in a search's allowance before one more profile: its own, and the two an exact apply rechecks with
# v2: a v1 pass that ran in the Deep Permuter's batch right after the profile sweep kept no scores for the pairs the sweep
# had just judged (deep_profiles.probe read only their queued outcome), and was still marked finished with no note: a
# new signature redoes those passes once, from the judge's cache, so they need no compile
# v3: the project's most used per-function profiles are planned too, so passes finished without them run again (cached
# verdicts need no compile)
# v4: every -O level of the function's compiler is judged, so passes finished without them run again
# v5: the draft's record arrays are judged under -O1 too (judge_record_arrays)
# v6: -fforce-addr for targets that read offsets off a held symbol address
# v7: the draft's address-named tables are judged as record arrays under -O1 too
# v8: the draft's float-bit arguments and switch width are judged under -O1 too (draft_constants)
# v9: ... and its unsigned-to-double conversions
# v10: kept other-profile attempts, missing draft externs, and clean_rewrite under -O0/-O1
SIGNATURE = 'profile-probe-v10:'
FP_FLAGS = ('-mfp32', '-mfp64')
# libultra's own names: osXxx, __osXxx, __rmon*, __sc*, the routines whose compiler was the library's, not the game's
LIBRARY_NAME = re.compile(r'^_{0,2}(?:os[A-Z]|rmon|sc[A-Z])')

@dataclass
class Facts:
    """What the rules read about one function: no database, no compiler."""
    own: dict  # the profile its saved C is judged under: {'toolchain', 'flags'}
    words: list[int] = field(default_factory=list)  # the target's instructions
    rows: list = field(default_factory=list)  # the diff of the saved C under `own`: [(marker, target, ours)]
    arch: str = 'mips'
    big_endian: bool = True
    name: str = ''
    vram: int = 0
    sdk: bool = False  # library detection names it a library function
    votes: Counter = field(default_factory=Counter)  # matched neighbours per (toolchain, flags)
    read: int = 0  # neighbours read
    common: Counter = field(default_factory=Counter)  # the project's functions per per-function (toolchain, flags)
    ready: Callable[[str], bool] = lambda toolchain: True  # whether a toolchain is installed and runs
    asm: str = ''
    source: str = ''
    header: str = ''


def key(profile) -> tuple:
    flags = profile['flags']
    return profile['toolchain'], tuple(flags.split() if isinstance(flags, str) else flags)


def probe(profile, family, why) -> dict:
    flags = profile['flags']
    return {'toolchain': profile['toolchain'], 'flags': flags.split() if isinstance(flags, str) else list(flags),
            'family': family, 'why': why}


# --- the decision -----------------------------------------------------------------------------------------------------

def explicit_fp(flags) -> bool:
    return any(f in FP_FLAGS for f in flags)


def mips_gcc(facts) -> list[dict]:
    """Probes that depend only on the MIPS GCC flags the profile names: the other floating-point register file, another
    `-G`, and both together (the float pool words and the `FP` rows of one function name each its own half)."""
    from . import fp_fingerprints, small_data_fingerprints
    own = facts.own
    if facts.arch != 'mips' or own.get('cmd') or not own.get('toolchain') or own['toolchain'].startswith('ido-'):
        return []
    flags = list(own['flags'])
    out = []
    code = b''.join(w.to_bytes(4, 'big' if facts.big_endian else 'little') for w in facts.words)
    fp = None
    if explicit_fp(flags):
        fp = fp_fingerprints.probe(own, code, facts.rows, facts.big_endian)
        if fp:
            out.append(probe({'toolchain': own['toolchain'], 'flags': fp}, 'fp',
                             fp_fingerprints.why(flags, code, facts.rows, facts.big_endian) or 'its floating-point registers'))
    if small_data_fingerprints.threshold(flags) is not None:
        for variant in small_data_fingerprints.probes(own, facts.rows):
            out.append(probe({'toolchain': own['toolchain'], 'flags': variant}, 'small_data',
                             small_data_fingerprints.why(flags, facts.rows) or 'its address rows'))
        # the float pool read through `$at` and the other register file together: neither alone moves the rows
        # of the other, so the diff under either flag alone shows the other's rows (-G8 -mfp32 in the hand matches)
        if fp and float_pool_through_at(facts.words) and small_data_fingerprints.threshold(flags) != 8:
            both = small_data_fingerprints.with_threshold(fp, 8)
            out.append(probe({'toolchain': own['toolchain'], 'flags': both}, 'small_data_fp',
                             'the target reads its float pool through $at (small data) and uses the other FP register file'))
    out += force_addr(facts, flags)
    return out


def force_addr(facts, flags) -> list[dict]:
    """`-fforce-addr`, under the function's own level and under `-O1`, for a target that reads offsets off a held symbol
    address: a rectangle-filling routine read `2($a2)`, `4($a2)`, `6($a2)` where every spelling under `-O1` and `-O2`
    gave absolute `%lo(sym + 2)` reads, and its plain C was exact under `-O1 -fforce-addr`."""
    if '-fforce-addr' in flags or not reads_through_held_address(facts.words):
        return []
    variants = [[*flags, '-fforce-addr']]
    lowered = ['-O1' if re.fullmatch(r'-O[23s]?', f) else f for f in flags]
    if lowered != flags:
        variants.append([*lowered, '-fforce-addr'])
    return [probe({'toolchain': facts.own['toolchain'], 'flags': variant}, 'force_addr',
                  'the target reads offsets off a register holding a symbol\'s address') for variant in variants]


def float_profile_hint(arch, own, code, big_endian=True) -> str:
    """One line for an open function's brief when its target was built with the other floating-point register file than
    its profile names, or ''. An N64 game's functions sat under `-mfp64` for days while agents rewrote source for
    register rows only `-mfp32` gives, and many matched on that flag alone: the target says so before the first compile.
    It reads code and flags only and changes no setting."""
    from . import fp_fingerprints
    flags = list(own.get('flags') or ())
    if arch != 'mips' or own.get('cmd') or not code or not own.get('toolchain') or own['toolchain'].startswith('ido-') \
            or not explicit_fp(flags):
        return ''
    why = fp_fingerprints.why(flags, bytes(code), (), big_endian)
    if why is None:
        return ''
    return (f"Compiler check (do this before rewriting source): the target suggests {' '.join(fp_fingerprints.flipped(flags))} "
            f"rather than this function's flags, because {why}. Judge your C under those flags with compiler_experiments; "
            "an exact result there is applied for this function only.")


def unoptimised_hint(arch, own, code, big_endian=True) -> str:
    """One line for an open function's brief when its target looks unoptimised and its profile optimises, or ''. The
    probe judges every -O level by itself, but an agent writes C first: a startup function sat at 1510 under `-O1`
    through two dozen attempts, and its plain C was exact under `-O0`. MIPS GCC only (IDO frames differ)."""
    flags = list(own.get('flags') or ())
    levels = [f for f in flags if re.fullmatch(r'-O[123s]?', f)]
    if arch != 'mips' or own.get('cmd') or not levels or not own.get('toolchain') \
            or own['toolchain'].startswith('ido-') or not keeps_frame_pointer(words_of(bytes(code or b''), big_endian)):
        return ''
    unoptimised = ' '.join('-O0' if f in levels else f for f in flags)
    return (f"Compiler check (do this before rewriting source): the target copies $sp into $fp, as unoptimised code "
            f"does. Write plain C (no do-while fences or padding) and judge it with compiler_experiments under "
            f"{unoptimised}; an exact result there is applied for this function only.")


def unscheduled_hint(arch, own, code, big_endian=True) -> str:
    """One line for an open function's brief when its target's prologue was not scheduled and its profile schedules
    it, or ''. The probe judges -O1 by itself, but only in a pass over C that exists: a saved C at 5112 under the
    project's `-O2` scored 107 under `-O1`, and plain C was exact there. MIPS GCC only."""
    flags = list(own.get('flags') or ())
    levels = [f for f in flags if re.fullmatch(r'-O[23s]', f)]
    if arch != 'mips' or own.get('cmd') or not levels or '-fno-schedule-insns2' in flags \
            or not str(own.get('toolchain') or '').startswith('gcc-') \
            or not saves_before_parameter_moves(words_of(bytes(code or b''), big_endian)):
        return ''
    lowered = ' '.join('-O1' if f in levels else f for f in flags)
    return (f"Compiler check (do this before rewriting source): the prologue stores every saved register before it "
            f"copies the parameters into them, as code built without GCC's second scheduler does. Write plain C and "
            f"judge it with compiler_experiments under {lowered} (or the current flags with -fno-schedule-insns2); an "
            f"exact result there is applied for this function only.")


def neighbour_profile_hint(net, proj, cfg, fn, own) -> str:
    """One line for an open function's brief when most of its matched neighbours were built with another profile, or ''.

    The probe judges that profile on the saved C and the draft, and says nothing when neither gets closer. But C tuned
    for the function's own settings is the wrong shape under the others: in a game built `-O2` by default, three
    functions sat at 130 and 445 with do-while fences and a padding array until plain C was written under the `-O1` of
    their neighbours, which was exact. So the agent is told before it writes, whatever the scores say."""
    from . import profile_detection
    mine = key(own)
    if own.get('cmd') or not own.get('toolchain'):
        return ''
    votes, read = profile_detection.neighbours(net, proj, cfg, fn)
    for other, count in votes.most_common(1):
        if other != mine and count >= NEIGHBOUR_MIN and count >= read * NEIGHBOUR_SHARE:
            flags = ' '.join(other[1])
            return (f"Compiler check (do this before rewriting source): {count} of its {read} nearest matched neighbours "
                    f"were built with {other[0]} {flags}, not this function's flags. C tuned for the current flags "
                    "(do-while fences, padding arrays, goto tails) usually does not carry over: write plain C and judge "
                    "it with compiler_experiments under those flags; an exact result there is applied for this function "
                    "only.")
    return ''


def neighbour(facts) -> list[dict]:
    """The profile most of the nearest matched functions were built with, when it is not this function's own: the
    address stretch a library or a module sits in (the matched audio code around an open function)."""
    mine = key(facts.own)
    out = []
    for other, count in facts.votes.most_common():
        if other != mine and count >= NEIGHBOUR_MIN and facts.read and count >= facts.read * NEIGHBOUR_SHARE:
            out.append(probe({'toolchain': other[0], 'flags': list(other[1])}, 'neighbours',
                             f'{count} of its {facts.read} nearest matched neighbours were built with it'))
    return out


def project_common(facts) -> list[dict]:
    """The per-function profiles most functions of the whole project were proven under, wherever they sit. A game whose
    default is `-O2` had a module's worth of `-O1` functions with no neighbour matched yet and nothing in their code
    that a rule reads: a dozen near-misses of 5-250 under their own settings were exact under it, several with the
    machine draft or the repairs alone."""
    mine = key(facts.own)
    used = [(other, count) for other, count in facts.common.most_common() if other != mine and count >= COMMON_MIN]
    return [probe({'toolchain': other[0], 'flags': list(other[1])}, 'project',
                  f'{count} functions of this project were built with it') for other, count in used[:COMMON_PROBES]]


def plan(console, facts) -> list[dict]:
    """The profiles worth one judge compile each, in the order to judge them: [{toolchain, flags, family, why}]. Empty
    when nothing in the target points anywhere. Never the function's own profile, never one that isn't installed."""
    seen = {key(facts.own)}
    chosen = []
    hints = console.profile_probes(facts)
    # a console's `lead` hint is a tell that settles the compiler's build outright (an `ldc1` and the assembler that
    # writes it): judged before the flag variants of the function's own compiler, which cannot fix a missing build
    ordered = [*neighbour(facts), *(h for h in hints if h.get('lead')), *mips_gcc(facts), *(h for h in hints if not h.get('lead')),
               *project_common(facts)]
    for item in ordered:
        k = key(item)
        if k in seen or not facts.ready(item['toolchain']):
            continue
        seen.add(k)
        chosen.append(item)
    return chosen[:MAX_PROBES]


def level_probes(facts) -> list[dict]:
    """The function's own flags at each other -O level of its compiler: no evidence needed, and on every processor."""
    from . import opt_levels
    own = facts.own
    if own.get('cmd') or not own.get('toolchain') or not facts.ready(own['toolchain']):
        return []
    return [probe({'toolchain': own['toolchain'], 'flags': flags}, 'level', 'another optimisation level of its compiler')
            for flags in opt_levels.variants(own['toolchain'], own['flags'])]


def with_levels(planned, levels) -> list[dict]:
    """The evidence's strongest first (the neighbours' vote, a console's settling hint), then the levels, then the rest:
    a profile in both is judged once, where it comes first."""
    first = [p for p in planned if p.get('lead') or p['family'] == 'neighbours']
    out, seen = [], set()
    for item in [*first, *levels, *planned]:
        if key(item) not in seen:
            seen.add(key(item))
            out.append(item)
    return out


def closer_level(scores, level_keys, own_score, draft_own) -> list[dict]:
    """The entries of `scores` worth searching under: those of the one level that is clearly closer, lowest score first,
    or []. Closer means at most NOTE_AT of the same text's score under the own settings (`own_score` for the saved C,
    `draft_own` for the draft), or at most NOTE_AT of the saved best."""
    ranked = []
    for entry in scores:
        if entry['score'] is None or key(entry) not in level_keys or own_score is None:
            continue
        reference = draft_own if entry.get('draft') else own_score
        below = entry['score'] <= own_score * NOTE_AT
        if not below and (reference is None or entry['score'] > reference * NOTE_AT):
            continue
        ranked.append(((not below, entry['score'] / max(1, reference or own_score), entry['score']), entry))
    if not ranked:
        return []
    best = key(min(ranked, key=lambda item: item[0])[1])
    return sorted((entry for _, entry in ranked if key(entry) == best), key=lambda entry: entry['score'])


# --- running it ---------------------------------------------------------------------------------------------------------

def supported(net, proj) -> bool:
    """A local ROM project; the room host owns a room's compiler settings, a project-mode build its files'."""
    from . import profile_detection
    return profile_detection.supported(net, proj)


def gather(net, proj, fn, source, own=None) -> Facts | None:
    """The facts of one open function: its target, the diff of `source` under its own profile (one judge compile when
    no diff was kept), its library identity and its matched neighbours. None when it has no profile to compare with.
    `own`: judge it as built under that profile instead of the function's own (a benchmark of the rules on matched code)."""
    from ..project import compiler_for
    from . import profile_detection
    from .deep_profile_sweep import _ready
    cfg = net.cfg(proj)
    row = dict(fn)
    own = own or compiler_for(cfg, row['vram'], fn)
    if own.get('cmd') or not own.get('toolchain'):
        return None
    own = profile_detection._profile(own)
    arch = proj['arch'] if 'arch' in proj.keys() else 'mips'
    big = bool(proj['big_endian']) if 'big_endian' in proj.keys() else True
    votes, read = profile_detection.neighbours(net, proj, cfg, fn)
    common = Counter(key(p) for p in (cfg.get('function_compilers') or {}).values() if p.get('toolchain'))
    sdk = bool(LIBRARY_NAME.match(row['name']))
    if not sdk:
        try:
            from . import sdk_assembly
            sdk = row['id'] in sdk_assembly.detected(net, proj)
        except Exception:  # noqa: BLE001 - library identity only orders the probes
            sdk = False
    context = net._context(proj['id'], row['unit']) if hasattr(net, '_context') else None
    return Facts(own=own, words=words_of(bytes(row.get('target') or b''), big), arch=arch, big_endian=big,
                 rows=profile_detection.saved_rows(net, proj, fn, own) if arch == 'mips' else [],
                 name=row['name'], vram=row['vram'] or 0, sdk=sdk, votes=votes, read=read, common=common, ready=_ready,
                 asm=row.get('asm') or '', source=source, header=context['header'] if context else '')


def marker_key(net, fn, source, own) -> str:
    from . import agent_tokens
    scope = agent_tokens.target_scope(net, {**dict(fn), 'best_source': source}, '')
    return SIGNATURE + hashlib.sha256(repr((scope, source, key(own))).encode()).hexdigest()


def finished(net, proj, fn, source, own) -> dict | None:
    """The record of a complete pass over this saved C and these build inputs, or None."""
    found = net.project_store(proj['id']).one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?',
                                              fn['id'], marker_key(net, fn, source, own))
    try:
        return json.loads(found['feedback']) if found else None
    except (TypeError, ValueError):
        return None


def pending(net, proj, fn) -> bool:
    """Whether this open function's saved C has not had its probe (or a closer level still waits to be searched): what
    a Deep Permuter pass asks about a function it would otherwise skip as already finished."""
    from ..project import compiler_for
    from . import deep_permuter, opt_levels
    try:
        if not supported(net, proj) or fn['status'] != 'open' or not fn['best_source'] or fn['best_score'] is None:
            return False
        own = compiler_for(net.cfg(proj), fn['vram'], fn)
        if own.get('cmd') or not own.get('toolchain') or not opt_levels.variants(own['toolchain'], own['flags']):
            return False
        record = finished(net, proj, fn, deep_permuter._candidate_source(fn, fn['best_score']),
                          {'toolchain': own['toolchain'], 'flags': own['flags']})
    except Exception:  # noqa: BLE001 - a function that can't be asked about is left to its next full pass
        return False
    return record is None or bool(record.get('lead') and not record.get('searched'))


_C_BUILTIN_TYPES = frozenset({
    'u8', 's8', 'u16', 's16', 'u32', 's32', 'u64', 's64', 'f32', 'f64',
    'char', 'short', 'int', 'long', 'unsigned', 'signed', 'float', 'double', 'void',
    'M2C_UNK', 'M2C_UNK8', 'M2C_UNK16', 'M2C_UNK32',
})
_C_KEYWORDS = frozenset({
    'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default', 'break', 'continue', 'return',
    'goto', 'sizeof', 'struct', 'union', 'enum', 'typedef', 'extern', 'static', 'const', 'volatile',
    'register', 'inline', 'NULL', 'TRUE', 'FALSE', 'true', 'false',
})


def complete_draft_externs(net, proj, fn, draft: str, header: str) -> str:
    """Synthesize fallback `extern` declarations for undeclared `D_...` or project data symbols referenced in `draft`
    so `m2c` drafts with missing globals can compile and be judged under alternate `-O` levels."""
    from decomprepair.csource import _blank, find

    from .global_types import declaration_text
    from .polish_scaffolding import DECLARATION, DECLARED, PRELUDE

    if not draft:
        return draft
    span = find(draft, fn['name'])
    if not span:
        return draft
    clean = _blank(draft)
    opening = clean.find('{', span[0], span[1])
    if opening < 0:
        return draft

    combined_head = _blank(header or '') + '\n' + clean[:span[0]]
    declared = {
        m[1] or m[2]
        for line in DECLARATION.findall(combined_head)
        for m in [DECLARED.search(line)]
        if m and (m[1] or m[2])
    }
    declared |= set(re.findall(r'^\s*#\s*define\s+([A-Za-z_]\w*)', (header or '') + '\n' + draft[:span[0]], re.M))
    known_types = set(_C_BUILTIN_TYPES) | set(
        re.findall(r'\btypedef\b[^;{}]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*;', combined_head)
    )

    sig_m = re.search(r'\(([^{};]*)\)\s*$', clean[span[0]:opening])
    params = {
        m[1]
        for part in (sig_m[1].split(',') if sig_m else ())
        for m in [re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*$', part.strip())]
        if m and part.strip() not in ('', 'void')
    }
    body = clean[opening:span[1]]
    locals_decl = set(
        re.findall(
            r'(?:^|[;{}])\s*(?:(?:const|volatile|register|unsigned|signed|struct|union|enum)\s+)*'
            r'[A-Za-z_]\w*(?:\s*\*+\s*|\s+)([A-Za-z_]\w*)\s*(?=[;=\[,])',
            body,
        )
    )
    skip = declared | known_types | params | locals_decl | _C_KEYWORDS | {fn['name']}

    candidates: list[str] = []
    seen: set[str] = set()
    for hit in re.finditer(r'\b([A-Za-z_]\w*)\b', body):
        ident = hit[1]
        if ident in skip or ident in seen or ident.startswith('M2C_'):
            continue
        before = body[:hit.start()].rstrip()
        after = body[hit.end():].lstrip()
        if before.endswith(('.', '->')) or after.startswith(('(', ':')):
            continue
        seen.add(ident)
        candidates.append(ident)
    if not candidates:
        return draft

    sym_types: dict[str, str] = {}
    try:
        store = net.project_store(proj['id']) if hasattr(net, 'project_store') else None
        if store is not None and hasattr(store, 'all'):
            placeholders = ','.join('?' for _ in candidates)
            rows = store.all(
                f"SELECT name, c_type FROM symbols WHERE project_id=? AND kind<>'func' AND name IN ({placeholders})",
                proj['id'], *candidates,
            )
            sym_types = {r['name']: (r['c_type'] or '').strip() for r in (rows or ())}
    except Exception:  # noqa: BLE001 - fallback heuristics handle missing symbol stores
        sym_types = {}

    extern_lines: list[str] = []
    fallback_scalar = 'M2C_UNK' if 'M2C_UNK' in draft else 's32'
    for ident in candidates:
        is_d_sym = bool(re.fullmatch(r'D_[0-9A-Fa-f]{6,16}', ident))
        if not is_d_sym and ident not in sym_types:
            continue
        c_type = sym_types.get(ident) or ''
        base_words = [w for w in re.findall(r'[A-Za-z_]\w*', re.sub(r'\[[^\]]*\]', '', c_type))
                      if w not in {'const', 'volatile', 'struct', 'union', 'enum', 'unsigned', 'signed'}]
        if c_type and all(w in known_types for w in base_words):
            extern_lines.append(f'extern {declaration_text(c_type, ident)};')
        elif re.search(rf'\b{re.escape(ident)}\s*\[', body):
            extern_lines.append(f'extern u8 {ident}[];')
        elif re.search(rf'\*\s*{re.escape(ident)}\b|\b{re.escape(ident)}\s*->', body):
            extern_lines.append(f'extern u8 *{ident};')
        else:
            extern_lines.append(f'extern {fallback_scalar} {ident};')
    if not extern_lines:
        return draft

    prelude = PRELUDE.search(draft[:span[0]])
    insert_at = prelude.end() if prelude else 0
    block = '\n'.join(extern_lines) + '\n'
    if insert_at and not draft[:insert_at].endswith('\n'):
        block = '\n' + block
    return draft[:insert_at] + block + draft[insert_at:]


def _kept_profile_attempt(net, proj, fn) -> dict | None:
    try:
        from . import profile_attempts
        return profile_attempts.closer(net, proj, fn)
    except Exception:  # noqa: BLE001 - unit stubs without SQLite tables have no kept attempt
        return None


def draft_under_own(search, agent, proj, fn, draft, own) -> int | None:
    """The draft's score under the function's own settings: what its score at another level is compared with."""
    from ..network import NetworkError
    from ..toolchains import ToolchainError
    from . import ido_compat
    net = search.net
    if not hasattr(net, '_judge_with'):
        return None
    search.check()
    search.tested += 1
    try:
        draft = ido_compat.adapted(own['toolchain'], draft)
        exact, diff, _ = net._judge_with(proj, fn, own['toolchain'], list(own['flags']), draft)
        if exact:  # (the ordinary recheck and save: a draft that matches as it is)
            net.try_quietly(agent, proj, fn, draft, exact_only=True)
            return 0
    except (NetworkError, ToolchainError) as error:
        search.log(f'The machine draft could not be judged under its own settings: {error}')
        return None
    return max(1, diff.score) if diff is not None else None


def run(search, agent, proj, fn, source, *, climb=True) -> bool:
    """Judge `source` and the machine draft under the planned profiles, and with `climb` search under a clearly closer
    -O level. True when one was exact (applied, or the exact pair is waiting on the project page: the search says
    which). Compiles count against the search's own allowance and stop with its Stop."""
    from .. import consoles
    from . import deep_profiles, project_activity
    from .function_names import canonical_source
    net = search.net
    if not (supported(net, proj) and source and fn['status'] == 'open' and project_activity.active(net, proj['slug'])):
        return False
    console = consoles.of(net.cfg(proj))
    try:
        facts = gather(net, proj, fn, source)
        if facts is None:
            return False
        before = finished(net, proj, fn, source, facts.own)
        if before is not None and not (climb and before.get('lead') and not before.get('searched')):
            return False
        levels = level_probes(facts) if hasattr(net, '_judge_with') else []  # (an engine with a judge)
        planned = with_levels(plan(console, facts), levels)
        level_keys = {key(item) for item in levels}
    except Exception:  # noqa: BLE001 - a planning fault must never stand between a function and its search
        import logging
        logging.getLogger('decompdecomp').exception('profile probe planning failed for %s', fn['name'])
        return False
    if not planned:
        return False
    seed = fn['seed'] if 'seed' in fn.keys() else None
    draft = complete_draft_externs(net, proj, fn, canonical_source(fn, seed), facts.header) if seed else None
    if draft and draft.strip() == source.strip():
        draft = None
    kept_attempt = _kept_profile_attempt(net, proj, fn)
    previous, search.phase = search.phase, 'profile probes'
    search.log(f'Profile probe: {len(planned)} per-function profile(s): '
               + '; '.join(f"{p['toolchain']} {' '.join(p['flags'])} ({p['family']})" for p in planned) + '.')
    scores, complete, matched = [], False, False  # (finished only when the loop below ends: a Stop or a spent allowance raises past it)
    draft_own = None
    try:
        for item in planned:
            search.check()
            if search.limit - search.tested < RESERVE:
                search.log('Profile probe stopped: the compile allowance is spent. A later pass tries the rest.')
                break
            profile = {'toolchain': item['toolchain'], 'flags': item['flags']}
            score = deep_profiles.probe(search, agent, proj, fn, source, profile, scored=True, review_variants=False)
            if score == 0:
                matched = True
                return True
            scores.append({**item, 'score': score})
            if (kept_attempt and key(kept_attempt) == key(item)
                    and kept_attempt.get('source', '').strip() not in {source.strip(), (draft or '').strip()}
                    and search.limit - search.tested >= RESERVE):
                search.check()
                kept_score = deep_profiles.probe(search, agent, proj, fn, kept_attempt['source'], profile,
                                                 scored=True, review_variants=False)
                if kept_score == 0:
                    matched = True
                    return True
                if kept_score is not None:
                    scores.append({**item, 'score': kept_score, 'source': kept_attempt['source']})
            if draft and search.limit - search.tested >= RESERVE:
                # the machine draft under the same profile: a sweep of one KMC game's open functions found 26 of the 64
                # that score at most half as much under -O1 through their draft, not their saved C
                search.check()
                drafted = deep_profiles.probe(search, agent, proj, fn, draft, profile, scored=True, review_variants=False)
                if drafted == 0:
                    matched = True
                    return True
                if drafted is not None:
                    scores.append({**item, 'score': drafted, 'draft': True})
            if item['family'] == 'level' and ('-O1' in item['flags'] or '-O0' in item['flags']):
                # spellings that only the old level tells apart: no better under the own settings, so no search keeps them
                matched, found = judge_record_arrays(search, agent, proj, fn, draft or source, facts, item, profile)
                if matched:
                    return True
                scores.extend(found)
            if item['family'] in ('small_data', 'small_data_fp') and score is not None:
                # the externs the target splits, declared as arrays above the threshold (extern_size_widen)
                from . import small_data_fingerprints
                for child in small_data_fingerprints.widened(item['flags'], source, fn['name'], facts.header, facts.asm):
                    if search.limit - search.tested < RESERVE:
                        break
                    search.check()
                    wide = deep_profiles.probe(search, agent, proj, fn, child, profile, scored=True, review_variants=False)
                    if wide == 0:
                        matched = True
                        return True
                    if wide is not None and wide < score:
                        scores.append({**item, 'family': item['family'] + '_arrays', 'score': wide})
        else:
            if draft and levels and search.limit - search.tested >= RESERVE:
                draft_own = draft_under_own(search, agent, proj, fn, draft, facts.own)
                if draft_own == 0:
                    matched = True
                    return True
            complete = True
    finally:
        search.phase = previous
        if not matched and (scores or complete):
            lead = closer_level(scores, level_keys, fn['best_score'], draft_own)
            record = diagnose(search, agent, proj, fn, source, facts.own, scores, complete, lead=lead,
                              draft_own=draft_own, said=before is not None)
    if not (climb and complete and lead):
        return False
    from . import ido_compat, profile_climb
    search.phase = 'closer optimisation level'
    try:
        texts = [({'toolchain': e['toolchain'], 'flags': e['flags']},
                  ido_compat.adapted(e['toolchain'], e.get('source') or (draft if e.get('draft') else source)),
                  e['score']) for e in lead]
        spent = search.tested
        matched = profile_climb.explore_level(search, agent, proj, fn, texts)
    finally:
        search.phase = previous
    if not matched and search.tested > spent:  # (no allowance for a single compile: a later pass searches)
        keep(net, proj, fn, source, facts.own, {**record, 'searched': True})
    return matched


def draft_constants(text, name, facts, flags) -> list[tuple[str, str]]:
    """`text` with the float bit patterns m2c passed as integers written as float arguments, its expansions of
    unsigned-to-double conversions written back, and its switch as wide as the target's range check, as one child.
    Each repair alone left a call-heavy dispatcher short of exact: its machine draft scored 2275 under `-O1`, 250 with
    the floats, and 0 with the empty `case` too. A spawner's draft scored 2975, and 2390 with the floats and the
    conversions."""
    from decomprepair import float_bit_argument_repairs, switch_range_repairs, unsigned_double_bias_fold
    child = None
    for _, repaired in float_bit_argument_repairs.variants(text, name, facts.header, facts.arch, flags)[:1]:
        child = repaired
    for _, repaired in unsigned_double_bias_fold.variants(child or text, name, facts.asm)[:1]:
        child = repaired
    for _, repaired in switch_range_repairs.variants(child or text, name, facts.asm)[:1]:
        child = repaired
    return [('draft_constants', child)] if child else []


def judge_record_arrays(search, agent, proj, fn, text, facts, item, profile) -> tuple[bool, list[dict]]:
    """`text` with its byte offsets off one global written as the record array declared there, judged under `profile`:
    (one was exact, the scores). A table initialiser matched only as that array under `-O1`, from its machine draft
    that scored the same under either level.

    Address-named parallel tables indexed by `i * STRIDE` (m2c's usual draft) are tried the same way
    (indexed_symbol_records): a machine draft at 7690 scored 550 as its record array under `-O1`, three steps from
    exact.

    The draft's float constants and switch width (draft_constants) are tried first.

    So is a display list written through a `Gfx **` argument, as the SDK's macros with the parameters' widths
    (gbi_param_shapes): five glyph builders' drafts scored 13000 to 23500 under `-O2` either way, and 0 only as that
    source under `-O1`."""
    from decomprepair import (
        clean_rewrite,
        gbi_param_shapes,
        global_offset_records,
        indexed_symbol_records,
    )

    from . import deep_profiles
    found = []
    flags = profile['flags']
    children = [*draft_constants(text, fn['name'], facts, flags.split() if isinstance(flags, str) else flags),
                *global_offset_records.variants(text, fn['name'], facts.header),
                *indexed_symbol_records.variants(text, fn['name'], facts.header, facts.asm),
                *gbi_param_shapes.variants(text, fn['name'], facts.header),
                *clean_rewrite.variants(text, fn['name'], facts.header)]
    for family, child in children:
        if search.limit - search.tested < RESERVE:
            break
        search.check()
        score = deep_profiles.probe(search, agent, proj, fn, child, profile, scored=True, review_variants=False)
        if score == 0:
            return True, found
        if score is not None:
            label = ('level_constants' if family == 'draft_constants'
                     else 'level_clean' if family.startswith('clean_rewrite')
                     else 'level_records')
            found.append({**item, 'family': label, 'score': score, 'draft': True, 'source': child})
    return False, found


def keep(net, proj, fn, source, own, record) -> None:
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (fn['id'], marker_key(net, fn, source, own), 'probed', 'profile probe', json.dumps(record)))


def diagnose(search, agent, proj, fn, source, own, scores, complete, *, lead=(), draft_own=None, said=False) -> dict:
    """Keep what the probes scored and tell the next agent the best of it, once (`said`: an earlier pass over this C
    already did). Exact pairs were handled by the judge call; this is only the non-exact ones. A pass the allowance cut
    short is not recorded as finished. Returns the record."""
    from ..network import NetworkError
    net = search.net
    scored = [s for s in scores if s['score'] is not None]
    best = min(scored, key=lambda s: s['score'], default=None)
    stored_probes = [{k: v for k, v in s.items() if k != 'source'} for s in scores]
    record = {'own_score': fn['best_score'], 'draft_own_score': draft_own, 'probes': stored_probes, 'complete': complete,
              'lead': bool(lead), 'searched': False,
              'best': best and {**{k: best[k] for k in ('toolchain', 'flags', 'family', 'score')},
                                **({'draft': True} if best.get('draft') else {})}}
    if complete:
        keep(net, proj, fn, source, own, record)
    if said or fn['best_score'] is None:
        return record
    if best is None or best['score'] > fn['best_score'] * NOTE_AT:
        # a level closer for the draft than its own settings are, though not closer than the saved C
        best = next((e for e in lead if e.get('draft')), None)
        if best is None or draft_own is None:
            return record
    settings = f"{best['toolchain']} {' '.join(best['flags'])}"
    which = 'the machine draft (get_function section=draft)' if best.get('draft') else 'this saved C'
    against = (f"{draft_own} under its own settings (the saved C: {fn['best_score']})"
               if best.get('draft') and draft_own is not None and best['score'] > fn['best_score'] * NOTE_AT else
               f"{'the saved C scores ' if best.get('draft') else ''}{fn['best_score']} under its own settings")
    line = (f"Profile probe: {which} scores {best['score']} under {settings} ({best['why']}), "
            f"{against}; not exact. Nothing was changed.")
    search.log(f"{fn['name']}: {line}")
    if complete and not any(s['score'] == 0 for s in scores):
        try:
            net.add_note(agent, proj['slug'], line + ' Try its C with that compiler (try_compiler) before more source '
                         'spellings; an exact result there is applied for this function only.', fn['name'],
                         score_before=fn['best_score'], score_after=best['score'])
        except NetworkError as error:
            search.log(f'Profile probe not noted on the function: {error}')
    return record
