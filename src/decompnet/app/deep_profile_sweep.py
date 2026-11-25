"""The deep permuter's first move: the saved C under the project's other compiler profiles, before any rewriting.

A function that already matches under another compiler would otherwise spend its whole allowance on permutations.
The profiles are the ones the project defines (every profile its functions use, and its default). A project with
only one has nothing to compare, so one profile per other compiler family of the console stands in (the first
installed one of each family, at the console's default flags). Each goes through deep_profiles.probe: cached per
profile, and only a rechecked exact source/profile pair can change settings. For a near-match it is compiler
detection as well (profile_detection): every installed profile, the ones the evidence points to first, and a profile
that scores the C clearly better is recorded, noted, suggested for review and tried first by the permuter.
"""
from collections import Counter

from .. import consoles, toolchains
from ..project import compiler_for
from . import deep_profiles

MAX_PROFILES = 16


def _key(profile):
    return profile['toolchain'], tuple(profile['flags'])


def _ready(tid):
    try:
        return bool(toolchains.installed(tid) and toolchains.get(tid).supported and not toolchains.runtime_error(tid))
    except Exception:  # (an id this install doesn't know)
        return False


def _family(tid):
    return toolchains.compiler_family(tid) or tid


def profiles(net, proj, fn):
    """The profiles to try on `fn`'s saved C, never its own settings (the baseline measures those)."""
    cfg = net.cfg(proj)
    own = deep_profiles._profile(compiler_for(cfg, fn['vram'], fn))
    defined = {}
    for profile in [deep_profiles._profile(cfg['compiler'])] + [
            deep_profiles._profile(p) for p in sorted(net.compiler_profile_usage(proj), key=lambda p: (-p['matched'], _key(p)))]:
        defined.setdefault(_key(profile), profile)
    if len(defined) > 1:
        chosen = list(defined.values())
    else:
        # one profile defined: one per compiler family the console offers
        chosen, families = [], {_family(own['toolchain'])}
        for option in consoles.of(cfg).compilers_for(cfg):
            family = _family(option.id)
            if option.automatic and family not in families and _ready(option.id):
                families.add(family)
                chosen.append({'toolchain': option.id, 'flags': list(consoles.default_flags(cfg, option.id))})
    return [p for p in chosen if _key(p) != _key(own) and _ready(p['toolchain'])][:MAX_PROFILES]


def run(search, agent, proj, fn, source):
    """True when one of them matched the saved C exactly (applied, or an error says what it waits for). For a
    near-match the sweep is compiler detection too (profile_detection): every installed profile of the console, the
    evidence's first, and a profile that scores the C clearly better is recorded, told and tried first."""
    from . import profile_detection
    chosen = profiles(search.net, proj, fn)
    detecting = profile_detection.supported(search.net, proj) and profile_detection.near_match(fn)
    reasons, votes, read = {}, Counter(), 0
    if detecting:
        chosen, reasons, votes, read = profile_detection.candidates(search.net, proj, fn, chosen)
    if not chosen:
        return False
    previous, search.phase = search.phase, 'compiler detection' if detecting else 'compiler profile sweep'
    search.log(f'Trying the saved C under {len(chosen)} other compiler profile(s) before any permutation'
               + (f" (its best, {fn['best_score']}, is a near-match: which compiler built it?)" if detecting else '')
               + ': ' + ', '.join(profile_detection.settings(p) if detecting else p['toolchain'] for p in chosen) + '.')
    scores = []

    def sweep(profiles):
        """True on an exact pair; the scores go to `scores`."""
        for profile in profiles:
            search.check()
            if search.limit - search.tested <= 0:
                if detecting:
                    search.log(f'Compiler detection stopped after {len(scores)} profile(s): the compile allowance '
                               'is spent. A later pass tries the rest.')
                return False
            score = deep_profiles.probe(search, agent, proj, fn, source, profile, **({'scored': True} if detecting else {}))
            if score == 0:
                return True
            scores.append((profile, score))
        return False
    try:
        if sweep(chosen):
            return True
        if detecting and search.limit - search.tested > 0:
            # (the saved C scored worse under another `-G` alone, but its small externs may need declaring as arrays too)
            own = compiler_for(search.net.cfg(proj), fn['vram'], fn)
            for profile, child in profile_detection.small_data_pairs(search.net, proj, fn, own):
                search.check()
                if search.limit - search.tested <= 0:
                    break
                search.log(f"Trying {profile_detection.settings(profile)} with the small externs declared as arrays.")
                if deep_profiles.probe(search, agent, proj, fn, child, profile) == 0:
                    return True
        if (detecting and search.limit - search.tested > 0
                and (lead := profile_detection.refine_lead(search.net, proj, fn, scores, reasons)) is not None):
            # (the toolchain the evidence or the scores point to, with its other flag sets: SN's own default flags
            # weren't the game's)
            if sweep(profile_detection.refinements(search.net, proj, lead, [profile_detection._key(p) for p, _ in scores])):
                return True
        if detecting and (finding := profile_detection.conclude(fn['best_score'], scores, reasons, votes, read)):
            profile_detection.record(search, agent, proj, fn, finding)
            current = getattr(search, 'compiler_profiles', None)
            if current is not None:  # (this pass's candidates are probed under it first too, not only the next's)
                key = profile_detection._key(finding['profile'])
                search.compiler_profiles = [finding['profile']] + [p for p in current if profile_detection._key(p) != key]
        elif detecting and scores:
            ranked = sorted((s, profile_detection.settings(p)) for p, s in scores if s is not None)
            if ranked:
                search.log(f"Compiler detection: no profile scores the saved C clearly better than its own settings "
                           f"({fn['best_score']}); the closest was {ranked[0][1]} at {ranked[0][0]}.")
    finally:
        search.phase = previous
    return False
