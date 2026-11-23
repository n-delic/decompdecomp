"""Which installed compiler profile a near-matched function was built with: the judge's scores for its saved C under
each profile, and the profiles its matched neighbours were judged with.

An N64 game built with KMC GCC 2.7.2 had eight near-matches stuck at score 10 under the project's profile; agents
spent hours on source spellings. They were SN GCC 2.8.1 code (`-G0 -mips2 -O2`): under it the same saved C was exact
or one trivial edit away. What named it: the target's `lui $v0; lw $v1,%lo(D)($v0)`, where KMC loads through its own
address register; zero arguments copied from a register just zeroed, where KMC sets each from `$zero`; float literals
loaded with `lui $at; lwc1`, where KMC builds them in `$at`; and the matched functions around them, judged with SN.
The deep permuter's first move (deep_profile_sweep) only ever tried one compiler per family (SN and KMC are both
GCC), or only the profiles the project used, and listened only for an exact match.

So, for a function whose saved best is a near-match (NEAR_MAX), the sweep scores that C under every installed
profile of its console with the judge (deep_profiles.probe: one compile each, cached per source and profile, counted
against the run's compile allowance), the profiles the evidence points to first: matched neighbours, the compiler
map, how its moves are written (move_fingerprints) and its code habits (compiler_fingerprints). Then, here:

- exact: deep_profiles.probe has already rechecked the pair and suggested it (applied only when the project lets
  proven compiler changes apply themselves, through suggest's own rules);
- clearly better (at most 1/CLEARLY of its own settings' score), or better and backed by its matched neighbours
  (STRONG of them on that profile): the finding is recorded for this saved C (a reuse_trials row), written to the
  function's notes and the run log, and offered as a words-only suggestion on the project page (suggest refuses a
  compiler its C doesn't match yet, and the user decides a project setting). The permuter then probes every
  candidate under that profile first (deep_profiles.selected) and climbs under it (profile_climb.explore).

Nothing here changes a function's compiler or credits a score. ROM projects only: a project-mode build gives each
source file its compiler (Console.file_compiler), which this doesn't read yet (ROADMAP.md, "Compiler detection for
project mode"). Rooms use the host's compiler settings.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

NEAR_MAX = 300  # a near-match: its saved best at most this far off (an extra instruction costs 100, a register 5)
CLEARLY = 2  # a profile scoring the saved C at most 1/CLEARLY of its own settings' score is a finding by itself
NEIGHBOUR_REACH = 0x4000  # bytes either side of the function its matched neighbours are read from
NEIGHBOURS = 12  # ... the nearest of them
STRONG = 3  # matched neighbours on one other profile that make it the function's likely compiler
STRONG_SHARE = 2 / 3  # ... and at least this share of the neighbours read
MAX_PROFILES = 16
REFINE = 4  # more flag sets tried for the toolchain that scored best (refinements)
SIGNATURE = 'profile-detection-v1:'


def _flags(flags) -> list[str]:
    return flags.split() if isinstance(flags, str) else list(flags)


def _key(profile) -> tuple:
    return profile['toolchain'], tuple(_flags(profile['flags']))


def _profile(profile) -> dict:
    return {'toolchain': profile['toolchain'], 'flags': _flags(profile['flags'])}


def settings(profile) -> str:
    return (profile['toolchain'] + ' ' + ' '.join(_flags(profile['flags']))).strip()


def _row(fn) -> dict:
    return dict(fn) if not isinstance(fn, dict) else fn


def supported(net, proj) -> bool:
    """A local ROM project: a project-mode build owns its files' compilers, a room its host's."""
    return bool(hasattr(net, 'project_store') and proj and 'slug' in proj.keys() and getattr(net, 'is_rom', lambda p: True)(proj)
                and not net.fork_slug(proj['slug']) and proj['slug'] not in getattr(net, 'links', {}))


def near_match(fn) -> bool:
    row = _row(fn)
    score = row.get('best_score')
    return bool(row.get('status', 'open') == 'open' and row.get('best_source') and score is not None
                and 0 < score <= NEAR_MAX)


def neighbours(net, proj, cfg, fn) -> tuple[Counter, int]:
    """(matched neighbours per profile, neighbours read): the nearest matched functions either side, with the profile
    each was judged with (its range's, its own or the project's: compiler_for)."""
    from ..project import compiler_for
    row = _row(fn)
    if row.get('vram') is None or 'id' not in proj.keys():
        return Counter(), 0
    rows = net.project_store(proj['id']).all(
        "SELECT name,vram,rom_offset FROM functions WHERE project_id=? AND status='matched' AND id<>? "
        "AND vram BETWEEN ? AND ? ORDER BY ABS(vram-?), id LIMIT ?",
        proj['id'], row.get('id', -1), row['vram'] - NEIGHBOUR_REACH, row['vram'] + NEIGHBOUR_REACH, row['vram'],
        NEIGHBOURS)
    votes = Counter()
    for near in rows:
        profile = compiler_for(cfg, near['vram'], near)
        if not profile.get('cmd') and profile.get('toolchain'):
            votes[_key(profile)] += 1
    return votes, len(rows)


def _mapped(net, proj, fn, own) -> dict | None:
    """The compiler map's compiler for this address, when it isn't the kind the function builds with today."""
    from .. import compilermap
    try:
        cmap = net.compiler_map(proj) if hasattr(net, 'compiler_map') else None
    except Exception:  # noqa: BLE001 - the map is evidence only: a detection never fails for want of it
        return None
    label = cmap.of.get(_row(fn).get('vram')) if cmap is not None else None
    if not label or compilermap.kind(label) == compilermap.kind(_key(own)):
        return None
    return {'toolchain': label[0], 'flags': list(label[1])}


def evidence(net, proj, cfg, fn, own) -> tuple[list[dict], dict, Counter, int]:
    """(profiles the evidence points to, best first; key -> what points to each; neighbour votes; neighbours read)."""
    from . import compiler_fingerprints, move_fingerprints
    reasons: dict[tuple, list[str]] = {}
    ordered: list[dict] = []

    def add(profile, why):
        k = _key(profile)
        if k == _key(own):
            return
        if k not in reasons:
            reasons[k] = []
            ordered.append(_profile(profile))
        if why not in reasons[k]:
            reasons[k].append(why)
    votes, read = neighbours(net, proj, cfg, fn)
    for (toolchain, flags), n in votes.most_common():
        add({'toolchain': toolchain, 'flags': list(flags)}, f'{n} of {read} matched neighbours were judged with it')
    if (mapped := _mapped(net, proj, fn, own)) is not None:
        add(mapped, 'the compiler map puts its address in that compiler\'s stretch')
    row = _row(fn)
    if row.get('target') and 'arch' in proj.keys() and proj['arch'] == 'mips':  # (habits are read from MIPS code)
        try:
            for profile in move_fingerprints.profiles(net, proj, cfg, fn, own):
                add(profile, 'its register moves are written the way that compiler writes them')
            floor = compiler_fingerprints.own_agreement(proj, fn, own)
            for agree, profile in compiler_fingerprints.ranked(net, proj, cfg, fn, own, limit=MAX_PROFILES):
                if floor is not None and agree > floor and profile['toolchain'] != own['toolchain']:
                    add(profile, f'its code habits agree with that compiler better ({agree:+d} against {floor:+d})')
        except Exception:  # noqa: BLE001 - habits only order the profiles; the judge decides
            pass
    return ordered, reasons, votes, read


def candidates(net, proj, fn, base) -> tuple[list[dict], dict, Counter, int]:
    """The profiles to score the saved C under: the evidence's first, then `base` (deep_profile_sweep's), then, for a
    near-match, every installed automatic compiler of the console at its default flags (one per family missed SN GCC
    in a KMC GCC game: both are GCC). Never the function's own settings; at most MAX_PROFILES."""
    from .. import consoles
    from ..project import compiler_for
    from .deep_profile_sweep import _ready
    cfg = net.cfg(proj)
    own = _profile(compiler_for(cfg, fn['vram'], fn))
    ordered, reasons, votes, read = evidence(net, proj, cfg, fn, own)
    chosen = ordered + list(base)
    if near_match(fn):
        chosen += [{'toolchain': c.id, 'flags': list(consoles.default_flags(cfg, c.id))}
                   for c in consoles.of(cfg).compilers if c.automatic]
    seen, out = {_key(own)}, []
    for profile in chosen:
        if _key(profile) not in seen and _ready(profile['toolchain']):
            seen.add(_key(profile))
            out.append(_profile(profile))
    return out[:MAX_PROFILES], reasons, votes, read


def refine_lead(net, proj, fn, scores, reasons) -> dict | None:
    """The other toolchain worth more flag sets: the first scored one the evidence points to, else the one scoring
    the saved C best when that beats its own settings."""
    from ..project import compiler_for
    own = compiler_for(net.cfg(proj), fn['vram'], fn)['toolchain']
    scored = [(p, s) for p, s in scores if s is not None and p['toolchain'] != own]
    backed = next((p for p, _ in scored if reasons.get(_key(p))), None)
    if backed is not None:
        return backed
    best = min(scored, key=lambda item: item[1], default=None)
    score = _row(fn).get('best_score')
    return best[0] if best is not None and score is not None and best[1] < score else None


def refinements(net, proj, profile, tried) -> list[dict]:
    """At most REFINE more profiles of the toolchain that scored the saved C best: a MIPS GCC's 32-bit ISA (`-mips2`,
    without `-mfp64`/`-mgp64`: the N64 game's SN code above was `-G0 -mips2 -O2`, where SN's own default is
    `-mips3 ... -mfp64`), then its flag variants (search_flags'). None already in `tried`."""
    from .. import consoles
    from .deep_profile_sweep import _ready
    flags = _flags(profile['flags'])
    out = []
    if 'arch' in proj.keys() and proj['arch'] == 'mips' and '-mips3' in flags:
        out.append([('-mips2' if f == '-mips3' else f) for f in flags if f not in ('-mfp64', '-mgp64')])
    if hasattr(net, 'flag_variants'):
        out += [list(f) for f in net.flag_variants(profile['toolchain'], flags, consoles.of(net.cfg(proj)).flag_extras)]
    seen, chosen = set(tried), []
    for variant in out:
        candidate = {'toolchain': profile['toolchain'], 'flags': variant}
        if _key(candidate) not in seen and _ready(candidate['toolchain']):
            seen.add(_key(candidate))
            chosen.append(candidate)
    return chosen[:REFINE]


def conclude(own_score, scores, reasons, votes, read) -> dict | None:
    """The finding from (profile, score) pairs: the best-scoring profile when it scores the saved C clearly better
    than its own settings do, or better and STRONG matched neighbours were judged with it. None otherwise."""
    if own_score is None or own_score <= 0:
        return None
    best = None
    for profile, score in scores:
        if score is None or score >= own_score:
            continue
        k = _key(profile)
        backed = votes.get(k, 0) >= STRONG and read and votes[k] >= read * STRONG_SHARE
        if score * CLEARLY > own_score and not backed:
            continue
        if best is None or score < best['score']:
            best = {'profile': _profile(profile), 'score': score, 'own_score': own_score,
                    'reasons': list(reasons.get(k, [])), 'neighbours': votes.get(k, 0), 'read': read}
    return best


def _signature(net, proj, fn) -> str | None:
    from ..project import compiler_for
    row = _row(fn)
    if not row.get('best_source') or row.get('vram') is None:
        return None
    own = _profile(compiler_for(net.cfg(proj), row['vram'], fn))
    return SIGNATURE + hashlib.sha256(repr((row['best_source'], _key(own))).encode()).hexdigest()


def detected(net, proj, fn) -> dict | None:
    """The finding recorded for this function's saved C under its current settings, or None (none, or the C or its
    settings changed since)."""
    try:
        row = _row(fn)
        if not hasattr(net, 'project_store') or 'id' not in row or not proj or 'id' not in proj.keys():
            return None
        if (signature := _signature(net, proj, fn)) is None:
            return None
        found = net.project_store(proj['id']).one(
            'SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', row['id'], signature)
        return json.loads(found['feedback']) if found else None
    except Exception:  # noqa: BLE001 - a finding only orders profiles: without it the permuter goes on as before
        return None


def record(search, agent, proj, fn, finding) -> bool:
    """Keep the finding for this saved C, and tell the user and the agents, once: the run log, the function's notes
    and a words-only suggestion on the project page. True when it was new."""
    from ..network import NetworkError
    net, row = search.net, _row(fn)
    signature = _signature(net, proj, fn)
    if signature is None:
        return False
    store = net.project_store(proj['id'])
    known = store.one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?', row['id'], signature)
    with store.tx() as db:
        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (row['id'], signature, 'detected', 'compiler profile detection', json.dumps(finding)))
    profile, why = finding['profile'], '; '.join(finding['reasons']) or 'the judge\'s scores alone'
    line = (f"Compiler detection: {row['name']}'s saved C scores {finding['score']} under {settings(profile)}, "
            f"{finding['own_score']} under its own settings. Evidence: {why}.")
    search.log(line + ' The permuter tries that profile first for this function; its compiler is unchanged.')
    if known:
        return False
    try:
        net.add_note(agent, proj['slug'], line + ' Suggested on the project page for review; nothing was applied. '
                     'Try its C with that compiler (try_compiler) before more source spellings.',
                     row['name'], score_before=finding['own_score'], score_after=finding['score'])
    except NetworkError as error:
        search.log(f'Compiler detection not noted on the function: {error}')
    lead = f"{row['name']} looks built with {settings(profile)}:"
    if not any(item.get('status') == 'open' and str(item.get('text', '')).startswith(lead)
               for item in net.cfg(net.project_row(proj['slug'])).get('suggestions', [])):
        try:
            net.suggest(agent, proj['slug'], f"{lead} its saved C scores {finding['score']} under it against "
                        f"{finding['own_score']} under its own settings ({why}). Not exact yet, so nothing applies "
                        'by itself: once its C matches under it, the exact pair is suggested for that function.')
        except NetworkError as error:
            search.log(f'Compiler detection not suggested: {error}')
    return True
