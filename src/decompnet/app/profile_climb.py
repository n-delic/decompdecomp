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

PROFILES_MEASURED = 6  # profiles measured on the current C before choosing one to climb under
CLIMB_SHARE = 4  # at most this fraction of the remaining compile allowance (1/4) goes to the climb
CLIMB_MAX = 400  # ... and never more than this many compiles
RANDOM_PER_ROUND = 24  # random rewrites tried after each round of local proposals


def lead(search, agent, proj, fn, source, own_score):
    """(profile, score) of the measured profile that scores the current C best, when strictly better than
    `own_score` (the function's own settings); None otherwise."""
    from ..project import compiler_for
    from . import compiler_fingerprints
    cfg = search.net.cfg(proj)
    own = compiler_for(cfg, fn['vram'], fn)
    best = None
    for _, profile in compiler_fingerprints.ranked(search.net, proj, cfg, fn, own, limit=PROFILES_MEASURED):
        search.check()
        if search.tested >= search.limit:
            break
        score = _judge(search, proj, fn, profile, source)
        if score is not None:
            search.log(f"Profile {profile['toolchain']} {' '.join(profile['flags'])} on the current C: score {score}")
        if score is not None and score < own_score and (best is None or score < best[1]):
            best = (profile, score)
    return best


def _judge(search, proj, fn, profile, source):
    from ..network import NetworkError
    from ..toolchains import ToolchainError
    search.tested += 1
    search.report()
    try:
        exact, diff, _ = search.net._judge_with(proj, fn, profile['toolchain'], profile['flags'], source)
    except (ToolchainError, NetworkError):
        return None  # (a profile this machine can't run, e.g. one needing a runner not prepared: skipped)
    if diff is None:
        return None
    return 0 if exact else max(1, diff.score)


def climb(search, agent, proj, fn, source, profile, score, seed=0):
    """Greedy search under `profile` from `source` (scoring `score` there). 0 when an exact pair was found and handed
    to deep_profiles.probe; else the best score reached under the profile (its C kept in search.profile_bests)."""
    from decomprepair.permuter import mutate

    from . import deep_profiles, draft_repairs
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
    search.log(f'Climbing under {key}: it scores the current C {score}, better than the function\'s own settings '
               f'(at most {budget} compiles).')
    rng = random.Random(f"{fn['id']}-{key}-{seed}")
    seen = {hashlib.sha256(source.encode()).hexdigest()}
    best_source, best = source, score
    spent = 0
    while spent < budget:
        improved = False
        labels = {}
        try:
            proposals = draft_repairs.variants(best_source, fn['name'], proj['arch'], fn['asm'] or '', profile['flags'],
                                               labels, limit=None, header=header)
        except Exception:  # noqa: BLE001 - a proposal generator's failure leaves the random rewrites
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
            search.check()
            spent += 1
            candidate = _judge(search, proj, fn, profile, text)
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
    return best
