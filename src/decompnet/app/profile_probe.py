"""The profile probe: an open function's saved C judged under the few per-function compiler profiles its target points to.

An N64 game's default profile (SN GCC 2.8.1, `-mfp64`) was wrong for about two-thirds of the ~1,000 functions an agent
matched by hand, and the right one was one of a small family each with a tell in the target's own code: another
floating-point register file (`-mfp32`), another small-data threshold (`-G8`, the float pool read through `$at`), an
older compiler (an epilogue `jr ra; nop` that SN GCC 2.8.1 never leaves; SN 2.7.2's builds), or a library's own compiler
(KMC GCC 2.7.2 for libultra). Agents found each by calling `compiler_experiments` function by function, slowly. This step
does it where the permuter and the autopilot already are, with no model:

1. decide (`plan`): from the saved C's diff, the target's bytes, the function's library identity and the profiles its
   matched neighbours were built with, which profiles are plausible. No evidence, no compile: a function with nothing
   pointing anywhere costs nothing. `Console.profile_probes` adds the console's own (consoles/n64/profile_probes.py:
   SN 2.7.2 builds and KMC for libultra); the MIPS GCC ones that depend only on flags (`-mfp32`, `-G`, and both) and the
   neighbours' vote are here.
2. judge (`run`): each profile through deep_profiles.probe, the same call the permuter's profile sweep uses, so a result
   is cached durably per (function, saved C, build inputs, profile) and never compiled twice, and an EXACT pair is
   rechecked and applied for that function only through `suggest`, with the project's own rule for automatic compiler
   changes (a project that doesn't apply them leaves the exact pair on the project page). The machine draft, when it is
   not the saved C, is judged under each profile too: under another profile it is often the closer text. Floating-point,
   small-data and scheduling variants apply like any other profile here: the permuter's sweep leaves them to review, but
   they are what this step is for, and only an exact, rechecked pair is ever applied.
3. say (`diagnose`): the best profile that was not exact is a note on the function ("-mfp32: 20 against 380") for the
   agent that picks it up next. It changes nothing: never a compiler range, never a non-exact profile.

Both callers hold the function the way they always do and stop through their own Stop: the Deep Permuter after its
profile sweep (deep_permuter.py) and the autopilot's saved-source pass (profile_probe_autopilot.py). A project nobody is
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

from ..arch.mips_profile_evidence import float_pool_through_at, words_of

MAX_PROBES = 10  # profiles judged per function and saved C: the plan is cut here, the rest wait for another pass
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
SIGNATURE = 'profile-probe-v3:'
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
    return out


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


def run(search, agent, proj, fn, source) -> bool:
    """Judge `source` under the planned profiles. True when one was exact (applied, or the exact pair is waiting on the
    project page: the search says which). Compiles count against the search's own allowance and stop with its Stop."""
    from .. import consoles
    from . import deep_profiles, project_activity
    net = search.net
    if not (supported(net, proj) and source and fn['status'] == 'open' and project_activity.active(net, proj['slug'])):
        return False
    console = consoles.of(net.cfg(proj))
    own_profile = None
    try:
        facts = gather(net, proj, fn, source)
        if facts is None:
            return False
        own_profile = facts.own
        if finished(net, proj, fn, source, own_profile) is not None:
            return False
        planned = plan(console, facts)
    except Exception:  # noqa: BLE001 - a planning fault must never stand between a function and its search
        import logging
        logging.getLogger('decompdecomp').exception('profile probe planning failed for %s', fn['name'])
        return False
    if not planned:
        return False
    seed = fn['seed'] if 'seed' in fn.keys() else None
    draft = seed if seed and seed.strip() != source.strip() else None
    previous, search.phase = search.phase, 'profile probes'
    search.log(f'Profile probe: {len(planned)} per-function profile(s) the target points to: '
               + '; '.join(f"{p['toolchain']} {' '.join(p['flags'])} ({p['family']})" for p in planned) + '.')
    scores, complete, matched = [], False, False  # (finished only when the loop below ends: a Stop or a spent allowance raises past it)
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
            complete = True
    finally:
        search.phase = previous
        if not matched and (scores or complete):
            diagnose(search, agent, proj, fn, source, facts.own, scores, complete)
    return False


def diagnose(search, agent, proj, fn, source, own, scores, complete) -> None:
    """Keep what the probes scored and tell the next agent the best of it, once. Exact pairs were handled by the judge
    call; this is only the non-exact ones. A pass the allowance cut short is not recorded as finished."""
    from ..network import NetworkError
    net = search.net
    scored = [s for s in scores if s['score'] is not None]
    best = min(scored, key=lambda s: s['score'], default=None)
    record = {'own_score': fn['best_score'], 'probes': scores, 'complete': complete,
              'best': best and {**{k: best[k] for k in ('toolchain', 'flags', 'family', 'score')},
                                **({'draft': True} if best.get('draft') else {})}}
    if complete:
        with net.project_store(proj['id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                       (fn['id'], marker_key(net, fn, source, own), 'probed', 'profile probe', json.dumps(record)))
    if best is None or fn['best_score'] is None or best['score'] > fn['best_score'] * NOTE_AT:
        return
    settings = f"{best['toolchain']} {' '.join(best['flags'])}"
    which = 'the machine draft (get_function section=draft)' if best.get('draft') else 'this saved C'
    line = (f"Profile probe: {which} scores {best['score']} under {settings} ({best['why']}), "
            f"{'the saved C scores ' if best.get('draft') else ''}{fn['best_score']} under its own settings; not exact. "
            "Nothing was changed.")
    search.log(f"{fn['name']}: {line}")
    if complete and not any(s['score'] == 0 for s in scores):
        try:
            net.add_note(agent, proj['slug'], line + ' Try its C with that compiler (try_compiler) before more source '
                         'spellings; an exact result there is applied for this function only.', fn['name'],
                         score_before=fn['best_score'], score_after=best['score'])
        except NetworkError as error:
            search.log(f'Profile probe not noted on the function: {error}')
