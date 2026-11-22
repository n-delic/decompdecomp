"""The deep permuter's first move: the saved C under the project's other compiler profiles, before any rewriting.

A function that already matches under another compiler would otherwise spend its whole allowance on permutations.
The profiles are the ones the project defines (every profile its functions use, and its default). A project with
only one has nothing to compare, so one profile per other compiler family of the console stands in (the first
installed one of each family, at the console's default flags). Each goes through deep_profiles.probe: cached per
profile, and only a rechecked exact source/profile pair can change settings.
"""
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
        for option in consoles.of(cfg).compilers:
            family = _family(option.id)
            if option.automatic and family not in families and _ready(option.id):
                families.add(family)
                chosen.append({'toolchain': option.id, 'flags': list(consoles.default_flags(cfg, option.id))})
    return [p for p in chosen if _key(p) != _key(own) and _ready(p['toolchain'])][:MAX_PROFILES]


def run(search, agent, proj, fn, source):
    """True when one of them matched the saved C exactly (applied, or an error says what it waits for)."""
    chosen = profiles(search.net, proj, fn)
    if not chosen:
        return False
    previous, search.phase = search.phase, 'compiler profile sweep'
    search.log(f'Trying the saved C under {len(chosen)} other compiler profile(s) before any permutation: '
               + ', '.join(p['toolchain'] for p in chosen) + '.')
    try:
        for profile in chosen:
            search.check()
            if search.limit - search.tested <= 0:
                break
            if deep_profiles.probe(search, agent, proj, fn, source, profile) == 0:
                return True
    finally:
        search.phase = previous
    return False
