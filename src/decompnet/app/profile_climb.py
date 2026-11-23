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
        for flags in (profile_hypotheses.kmc_cse_address_probe(own, rows), profile_hypotheses.kmc_expensive_probe(own, rows)):
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
    while spent < budget:
        improved = False
        labels = {}
        args = (best_source, fn['name'], proj['arch'], fn['asm'] or '', profile['flags'])
        pool = getattr(search, 'compile_pool', None)
        if pool is not None:
            # A broad generation on a worker thread can run past the shared
            # deadline. The proposal pool can interrupt its child process.
            proposals, labels, _ = pool.proposal_pool().generate(args, dict(limit=None, header=header), check)
        else:
            try:
                proposals = draft_repairs.variants(*args, labels, limit=None, header=header)
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
            return climb(search, agent, proj, fn, source, *found, require_clean=bool(clean_profile), check=check) == 0
    except _ProfileTimeElapsed:
        search.log('Alternate compiler profile time share reached; continuing with the current settings.')
    return False
