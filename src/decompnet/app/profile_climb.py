"""A deep pass that climbs under another compiler profile, when the function's code points to one that fits better.

Other profiles used to be exact-only probes: each candidate was compiled with them, and only an exact match counted.
When the C also has to change, the right profile scores the current C better but every step toward the match is
judged under the function's own settings, where it looks no better, so the search never moved toward it. Three N64
functions whose stack frame is made after their arithmetic were 120-155 points off under their game's compiler and
10 or exact under another installed one at its own flags, with the C unchanged.

After the local repairs, the profiles that compiler_fingerprints ranks best for the function (and its own
compiler's flag variants) are measured on the current C. When one scores strictly better than the function's own
settings, a bounded greedy search runs under it: the same proposals the local repairs use (draft_repairs) and the
library's random rewrites, each judged with the judge's linked verdict under that profile, keeping a best of its own.
A near-match also gets a short best-child chain of the repairs under the profiles its matched neighbours and the
project's other functions use, whether or not the C scores better there as it stands (explore_usual).
An exact result goes through deep_profiles.probe, which rechecks the source/profile pair and suggests the setting:
nothing here changes a function's settings or records a score under its own profile.
"""
from __future__ import annotations

import hashlib
import random
import time
from collections import Counter

PROFILES_MEASURED = 6  # profiles measured on the current C before choosing one to climb under
CLIMB_SHARE = 4  # at most this fraction of the remaining compile allowance (1/4) goes to the climb
CLIMB_MAX = 400  # ... and never more than this many compiles
RANDOM_PER_ROUND = 24  # random rewrites tried after each round of local proposals
CHAIN_SHARE = 2  # a floating-point profile's repair chain may use at most this fraction (1/2) of the climb's compiles
CHAIN_STEPS = 4  # ... in at most this many steps of best-child greedy search
CHAIN_CHILDREN = 100  # ... judging at most this many children a step
USUAL_NEIGHBOUR = 1  # profiles of matched neighbours given a repair chain although the current C scores worse there
USUAL_COMMON = 2  # ... and the project's most used per-function profiles
USUAL_COMPILES = 2 * CHAIN_CHILDREN  # compiles one such chain may spend
FP_FIRST = ('float_literal', 'double_literal', 'operand_order')  # families whose names say they fix FP constants/operands


def lead(search, agent, proj, fn, source, own_score, *, check=None):
    """(profile, score) to climb under: the measured profile that scores the current C best, when strictly better
    than `own_score` (the function's own settings); else the best-scoring profile whose habits agree with the code
    strictly more than the function's own settings do (compiler_fingerprints), even though the current C, tuned to
    the own settings, scores worse there. None when neither.

    The second case is a library built with scheduling off inside a scheduled game: its saved C (a strcmp) was 130
    off under the game's settings and 890 off with scheduling off, written in the order the scheduler had made, while
    plain C was exact with scheduling off. Only ever climbing toward better scores never went there."""
    from ..project import compiler_for
    from . import compiler_fingerprints, profile_hypotheses, repair_ranking
    check = check or search.check
    cfg = search.net.cfg(proj)
    own = compiler_for(cfg, fn['vram'], fn)
    floor = compiler_fingerprints.own_agreement(proj, fn, own)
    best = closer = None
    ranked = compiler_fingerprints.ranked(search.net, proj, cfg, fn, own, limit=PROFILES_MEASURED)
    if proj['arch'] == 'mips':
        rows = repair_ranking.instruction_rows(repair_ranking.evidence(search.net, fn['id'], getattr(agent, 'id', None)))
        from . import fp_fingerprints
        big_endian = bool(proj['big_endian']) if 'big_endian' in proj.keys() else True
        for flags in (profile_hypotheses.kmc_cse_address_probe(own, rows), profile_hypotheses.kmc_cse_hoist_probe(own, rows),
                      profile_hypotheses.kmc_cse_skip_probe(own, rows),
                      profile_hypotheses.kmc_expensive_probe(own, rows),
                      fp_fingerprints.probe(own, bytes(fn['target']) if 'target' in fn.keys() and fn['target'] else b'',
                                            rows, big_endian)):
            if not flags:
                continue
            profile = dict(own, flags=flags)
            # Keep the same total profile allowance. This lead has source-diff evidence.
            ranked = [(floor, profile), *((agree, p) for agree, p in ranked
                                          if p['toolchain'] != own['toolchain'] or p['flags'] != flags)][:PROFILES_MEASURED]
    for agree, profile in ranked:
        check()
        if search.tested >= search.limit:
            break
        score = _judge(search, proj, fn, profile, source)
        if score is None:
            continue
        if score == 0:
            return profile, score
        search.log(f"Profile {profile['toolchain']} {' '.join(profile['flags'])} on the current C: score {score}")
        if score < own_score and (best is None or score < best[1]):
            best = (profile, score)
        if floor is not None and agree is not None and agree > floor and (closer is None or score < closer[1]):
            closer = (profile, score)
    # The jump threader off, under the closest profile so far: one KMC state routine was exact under -O1 only with it,
    # its single differing row a branch the threader sent past a later test of the same value.
    lead_profile = best[0] if best else own
    if (proj['arch'] == 'mips' and lead_profile['toolchain'] == 'gcc-2.7.2-kmc'
            and not any(flag in lead_profile['flags'] for flag in ('-fno-thread-jumps', '-fthread-jumps'))
            and search.tested < search.limit):
        check()
        flags = lead_profile['flags']  # (a project's settings may spell them as one string)
        flags = flags.split() if isinstance(flags, str) else list(flags)
        profile = dict(lead_profile, flags=[*flags, '-fno-thread-jumps'])
        score = _judge(search, proj, fn, profile, source)
        if score == 0:
            return profile, score
        if score is not None and score < (best[1] if best else own_score):
            search.log(f"Profile {profile['toolchain']} {' '.join(profile['flags'])} on the current C: score {score}")
            best = (profile, score)
    if search.tested < search.limit:
        check()
        profile = neighbour_profile(search.net, proj, cfg, fn, [own, *(p for _, p in ranked)])
        if profile is not None:
            score = _judge(search, proj, fn, profile, source)
            if score == 0:
                search.log(f"Nearby matches suggested an exact {profile['toolchain']} profile.")
                return profile, score
    if best is None and closer is not None:
        search.log(f"Its code looks built with {closer[0]['toolchain']} {' '.join(closer[0]['flags'])} rather than its "
                   'own settings: climbing under it too, though the current C scores worse there.')
    return best or closer


def neighbour_profile(net, proj, cfg, fn, tested):
    """Try one other profile backed by two exact matches near this function in its unit."""
    from ..project import compiler_for

    if not hasattr(net, 'project_store') or fn['vram'] is None or 'cmd' in cfg['compiler']:
        return None
    vram = fn['vram']
    neighbours = net.project_store(proj['id']).all(
        "SELECT name,rom_offset,vram FROM functions WHERE project_id=? AND unit=? AND status='matched' "
        "AND best_score=0 AND id<>? AND vram BETWEEN ? AND ? ORDER BY ABS(vram - ?), id LIMIT 8",
        proj['id'], fn['unit'], fn['id'], vram-16384, vram+16384, vram,
    )

    def key(profile):
        flags = profile['flags']
        return profile['toolchain'], tuple(flags.split() if isinstance(flags, str) else flags)

    excluded = {key(profile) for profile in tested}
    counts = Counter(key(compiler_for(cfg, row['vram'], row)) for row in neighbours)
    for (toolchain, flags), votes in counts.most_common():
        if votes >= 2 and (toolchain, flags) not in excluded:
            return {'toolchain': toolchain, 'flags': list(flags)}
    return None


def usual_profiles(net, proj, cfg, fn, own) -> list[dict]:
    """The profiles other matched functions were built with, other than the function's own: the one most of its matched
    neighbours use (at least two), then the project's most used per-function profiles."""
    from .profile_detection import neighbours
    from .profile_probe import COMMON_MIN, key

    mine = key(own)
    votes, _ = neighbours(net, proj, cfg, fn)
    near = [k for k, n in votes.most_common() if n >= 2 and k != mine][:USUAL_NEIGHBOUR]
    used = Counter(key(p) for p in (cfg.get('function_compilers') or {}).values() if p.get('toolchain'))
    common = [k for k, n in used.most_common() if n >= COMMON_MIN and k != mine and k not in near][:USUAL_COMMON]
    return [{'toolchain': toolchain, 'flags': list(flags)} for toolchain, flags in near + common]


def explore_usual(search, agent, proj, fn, source, own_score, *, check) -> bool:
    """A short repair chain of a near-match under each of the profiles matched functions use, even where the C as it
    stands scores worse than under its own. True when one was exact (handed to deep_profiles.probe).

    A random-range helper of an N64 game was 15 under the project's KMC GCC and 25 under the SN profile most of its
    matched functions use, so no climb ever went there: exact there two repairs later (callee_arity_trim 15, then
    default_into_then 0), while the same two under KMC stopped at a reload register no C can choose."""
    from ..project import compiler_for
    from . import deep_profiles
    from .profile_detection import NEAR_MAX

    if not 0 < own_score <= NEAR_MAX:
        return False
    try:
        cfg = search.net.cfg(proj)
        own = compiler_for(cfg, fn['vram'], fn)
        if own.get('cmd'):
            return False
        profiles = usual_profiles(search.net, proj, cfg, fn, own)
    except Exception:  # noqa: BLE001 - this is one more lead: without the project's records there is none
        return False
    for profile in profiles:
        check()
        key = profile['toolchain'] + ' ' + ' '.join(profile['flags'])
        budget = min(USUAL_COMPILES, max(0, search.limit - search.tested) // CLIMB_SHARE)
        if budget <= 0:
            break
        if key in search.profile_bests:
            continue  # (the climb already searched under it)
        score = _judge(search, proj, fn, profile, source)
        if score is None or score > NEAR_MAX:
            continue
        best_source, best = source, score
        if score:
            search.log(f'Repair chain under {key}, which matched functions use, from score {score}.')
            header = search.net._context(proj['id'], fn['unit'])['header']
            best_source, best, _ = repair_chain(search, agent, proj, fn, source, score, profile, budget, check,
                                                header, key)
        search.profile_bests[key] = (best, best_source)
        if best == 0:
            deep_profiles.probe(search, agent, proj, fn, best_source, profile)
            return True
    return False


def _judge(search, proj, fn, profile, source, *, require_clean=False):
    from ..network import NetworkError
    from ..toolchains import ToolchainError
    search.tested += 1
    search.report()
    try:
        exact, diff, problems = search.net._judge_with(proj, fn, profile['toolchain'], profile['flags'], source)
    except (ToolchainError, NetworkError):
        return None  # (a profile this machine can't run, e.g. one needing a runner not prepared: skipped)
    if diff is None or (require_clean and problems):
        return None
    return 0 if exact else max(1, diff.score)


def proposal_options(search, proj, fn, source, header):
    """What the repair families are given besides the source: the jump tables, the callees' matched C, the opening
    scope and the words of the function's float constants, as the Deep Permuter's local lane and prove_chain give them.

    The climb used to give the families the header alone: float_literal_global and double_literal_global need the words
    (the value the C cannot show), so a climb under a floating-point profile never proposed the literal that, with the
    profile, made four N64 functions exact (a replay from their saved bests under -mfp32 stalled at 10 and 40 with
    no literal proposed; with the words the same families reached 0). A search with no project store (a fake, a
    fork project) gets the header alone."""
    options = dict(limit=None, header=header)
    try:
        net = search.net
        if net.fork_slug(proj['slug']):
            return options
        from . import draft_repairs, float_literal_words, switch_tables
        found = dict(tables=switch_tables.read(net, proj, fn),
                     callees=draft_repairs.capture_followup_repairs.callee_evidence(net, proj, fn, source),
                     entry_scope=draft_repairs.promoted_entry_repairs.scope(net, proj, fn),
                     words=float_literal_words.read(net, proj, fn))
    except Exception:  # noqa: BLE001 - context only widens the proposals: without it the climb goes on as before
        return options
    return {**options, **found}


def fp_variant(search, proj, fn, profile) -> bool:
    """True when `profile` is the function's own settings with only the floating-point register model flipped."""
    try:
        from ..project import compiler_for
        from .fp_fingerprints import is_fp_variant
        own = compiler_for(search.net.cfg(proj), fn['vram'], fn)
        return own['toolchain'] == profile['toolchain'] and is_fp_variant(own['flags'], profile['flags'])
    except Exception:  # noqa: BLE001 - not knowing the function's settings is not being an FP variant
        return False


def level_variant(search, proj, fn, profile) -> bool:
    """True when `profile` is the function's own settings at another -O level."""
    try:
        from ..project import compiler_for
        from . import opt_levels
        own = compiler_for(search.net.cfg(proj), fn['vram'], fn)
        return own['toolchain'] == profile['toolchain'] and list(profile['flags']) in opt_levels.variants(
            own['toolchain'], own['flags'])
    except Exception:  # noqa: BLE001 - not knowing the function's settings is not being a level variant
        return False


def _fp_first(options):
    """[(family, child)] with the FP-constant and operand families first (stable otherwise): a step judges at most
    CHAIN_CHILDREN of them, and the literal inlining that matches under the flipped profile is one child among hundreds."""
    return sorted(options, key=lambda item: not any(word in item[0] for word in FP_FIRST))


def repair_chain(search, agent, proj, fn, source, score, profile, budget, check, header, key):
    """Best-child greedy chain of the registered repair families, judged under `profile` (the flipped floating-point
    profile): what prove_chain does from a saved best, inside the climb. A match under the flipped profile alone is
    what profile_detection records; this is for the functions whose source also needs a repair there (float literals
    written inline, a commutative swap) which neither the profile nor the repair reaches alone: four N64 functions
    of one game were 70, 75 and 60 under -mfp64, 45, 40 and 30 under -mfp32 and exact under -mfp32 with the repair.

    Each step judges the children (at most CHAIN_CHILDREN, FP families first) and keeps the best one that beats the
    current score, up to CHAIN_STEPS steps and `budget` compiles. Returns (best source, best score, compiles spent).
    The profile is only the judging profile of this search: nothing here changes the function's settings."""
    from . import draft_repairs
    from .prove_chain import chain
    spent = [0]
    known = {source: score}
    pool = getattr(search, 'compile_pool', None)
    args_for = lambda text: (text, fn['name'], proj['arch'], fn['asm'] or '', profile['flags'])  # noqa: E731

    def generate(text):
        options = proposal_options(search, proj, fn, text, header)
        labels = {}
        if pool is not None:
            texts, labels, _ = pool.proposal_pool().generate(args_for(text), options, check)
        else:
            try:
                texts = draft_repairs.variants(*args_for(text), labels, **options)
            except Exception:  # noqa: BLE001 - a family's bug on an edited source must not end the search
                texts = []
        return _fp_first([(labels.get(child, 'unlabelled'), child) for child in texts])

    def judge(sources):
        scores, done = [], False
        for text in sources:
            if text in known:
                scores.append(known[text])
                continue
            if done or spent[0] >= budget or search.tested >= search.limit:
                scores.append(None)
                continue
            check()
            spent[0] += 1
            known[text] = result = _judge(search, proj, fn, profile, text)
            scores.append(result)
            done = result == 0  # (an exact child ends the step: the rest need no compile)
        return scores

    def limit():
        check()
        if spent[0] >= budget:
            raise TimeoutError  # (chain's own signal for "out of time": its budget is compiles here)

    final, best, taken, notes = chain(source, generate, judge, None, CHAIN_STEPS, CHAIN_CHILDREN, limit)
    for family, result, scored, offered in taken:
        search.log(f'Under {key}: {result} ({family}; {scored} of {offered} children judged)')
    return final, best if best is not None else score, spent[0]


def climb(search, agent, proj, fn, source, profile, score, seed=0, *, require_clean=False, check=None):
    """Greedy search under `profile` from `source` (scoring `score` there). 0 when an exact pair was found and handed
    to deep_profiles.probe; else the best score reached under the profile (its C kept in search.profile_bests)."""
    from decomprepair.permuter import mutate

    from . import deep_profiles, draft_repairs
    check = check or search.check
    if score == 0:
        # (the current C already matches under it: recheck and suggest the pair the ordinary way)
        search.profile_bests[profile['toolchain'] + ' ' + ' '.join(profile['flags'])] = (0, source)
        deep_profiles.probe(search, agent, proj, fn, source, profile)
        return 0
    budget = min(CLIMB_MAX, max(0, (search.limit - search.tested) // CLIMB_SHARE))
    if budget <= 0:
        return score
    header = search.net._context(proj['id'], fn['unit'])['header']
    key = profile['toolchain'] + ' ' + ' '.join(profile['flags'])
    search.log(f'Climbing under {key} from score {score} (at most {budget} compiles).')
    rng = random.Random(f"{fn['id']}-{key}-{seed}")
    seen = {hashlib.sha256(source.encode()).hexdigest()}
    best_source, best = source, score
    spent = 0
    if fp_variant(search, proj, fn, profile) or level_variant(search, proj, fn, profile):
        # (the flipped floating-point profile, or another -O level: the source may need a repair under it too: a
        # best-child chain first)
        best_source, best, spent = repair_chain(search, agent, proj, fn, source, score, profile,
                                                max(1, budget // CHAIN_SHARE), check, header, key)
        seen.add(hashlib.sha256(best_source.encode()).hexdigest())
        if best == 0:
            search.profile_bests[key] = (0, best_source)
            deep_profiles.probe(search, agent, proj, fn, best_source, profile)
            return 0
    while spent < budget:
        improved = False
        labels = {}
        args = (best_source, fn['name'], proj['arch'], fn['asm'] or '', profile['flags'])
        pool = getattr(search, 'compile_pool', None)
        options = proposal_options(search, proj, fn, best_source, header)
        if pool is not None:
            # A broad generation on a worker thread can run past the shared
            # deadline. The proposal pool can interrupt its child process.
            proposals, labels, _ = pool.proposal_pool().generate(args, options, check)
        else:
            try:
                proposals = draft_repairs.variants(*args, labels, **options)
            except Exception:  # noqa: BLE001 - standalone probes can still try random rewrites
                proposals = []
        for _ in range(RANDOM_PER_ROUND):
            text = mutate(best_source, rng, header=header)
            if text:
                proposals.append(text)
        for text in proposals:
            digest = hashlib.sha256(text.encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            if spent >= budget or search.tested >= search.limit:
                break
            check()
            spent += 1
            candidate = _judge(search, proj, fn, profile, text, require_clean=require_clean)
            if candidate is None or candidate >= best:
                continue
            best_source, best = text, candidate
            improved = True
            search.log(f'Under {key}: {candidate}' + (f" ({labels.get(text)})" if labels.get(text) else ''))
            if candidate == 0:
                # (the exact pair is rechecked and suggested the ordinary way; never applied here)
                search.profile_bests[key] = (0, best_source)
                deep_profiles.probe(search, agent, proj, fn, best_source, profile)
                return 0
            break
        if not improved:
            break
    search.profile_bests[key] = (best, best_source)
    search.log(f'Under {key}: best {best} after {spent} compiles.')
    from .matching_preflight import closer
    if note := closer(search.net, agent, proj['slug'], fn['name'], profile,
                       fn['best_score'] if 'best_score' in fn.keys() else None, best):
        search.log(note)
    return best


class _ProfileTimeElapsed(Exception):
    """The alternate-profile window ended while time remains for the main search."""


def explore_level(search, agent, proj, fn, leads) -> bool:
    """Repair under the -O level the profile probe found clearly closer: `leads` is [(profile, C, its score there)],
    one level, closest text first. Within the same share of the pass's time as `explore`; True when one was exact."""
    now = time.monotonic()
    deadline = now + max(0, search.deadline - now) / CLIMB_SHARE

    def check():
        search.check()
        if time.monotonic() >= deadline:
            raise _ProfileTimeElapsed

    try:
        for profile, source, score in leads:
            check()
            if climb(search, agent, proj, fn, source, profile, score, check=check) == 0:
                return True
    except _ProfileTimeElapsed:
        search.log('Optimisation level time share reached; continuing with the current settings.')
    return False


def explore(search, agent, proj, fn, baseline, clean_profile=None):
    """Give alternate profiles a quarter of the remaining time and return whether one matched."""
    now = time.monotonic()
    deadline = now + max(0, search.deadline - now) / CLIMB_SHARE

    def check():
        search.check()
        if time.monotonic() >= deadline:
            raise _ProfileTimeElapsed

    try:
        check()
        if clean_profile:
            source = clean_profile['source']
            profile = {key: clean_profile[key] for key in ('toolchain', 'flags')}
            found = profile, clean_profile['score']
        else:
            source = fn['best_source']
            from .profile_detection import detected, settings
            # (the profile compiler detection found for this C, already measured on it: climbed under first)
            if (finding := detected(getattr(search, 'net', None), proj, fn)) is not None:
                search.log(f"Climbing under the detected compiler profile {settings(finding['profile'])}.")
                found = finding['profile'], finding['score']
            else:
                found = lead(search, agent, proj, fn, source, baseline, check=check)
        if found is not None:
            if found[1] == 0:
                search.check()
            else:
                check()
            if climb(search, agent, proj, fn, source, *found, require_clean=bool(clean_profile), check=check) == 0:
                return True
        if not clean_profile:
            return explore_usual(search, agent, proj, fn, source, baseline, check=check)
    except _ProfileTimeElapsed:
        search.log('Alternate compiler profile time share reached; continuing with the current settings.')
    return False
