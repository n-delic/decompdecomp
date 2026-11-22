"""A machine draft taken from a close matched look-alike in the same game, judged beside m2c's.

When a function's instructions are nearly those of a function the game already matched (the similarity search
`find_similar` uses), that function's matched C with this function's names is often a closer start than m2c's draft.
The transplant is the one the similar-code reuse pass makes (`similar_matches`): the names come from the
relocations both functions fill at the same aligned instructions (`twins.rom_renames`), and it is refused when one
name would have to mean two things; constants the aligned immediates change are substituted where that is
unambiguous (`similar_matches.literal_variants`). Here it is only a draft candidate: the raw draft is judged first
and stays the stored seed, and a best is only replaced by a better one.

ROM mode, MIPS (where the similarity search reads instructions); project mode has twins by object fingerprints but
not this alignment yet (ROADMAP).
"""
import re

# Project setting (`twin_drafts`, default on): measured in docs/learnings/finding/draft-start-candidates.md.
SETTING = 'twin_drafts'
FLOOR = .9        # instruction similarity at least this (similar_matches.MIN_SIMILARITY)
DONORS = 2        # matched look-alikes tried, closest first
RATIO = .75       # sizes at most this far apart


def enabled(cfg) -> bool:
    return bool((cfg or {}).get(SETTING, True))


def transplant(donor, target, syms, pa, pb, ops_a, ops_b, header_a, header_b, big):
    """[source] for `target` from `donor`'s matched C (rows with name, target, relocs, matched_source), or [] when
    the names can't be mapped consistently. `pa`/`pb`: their prints (twins.rom_print), `ops_*` their mnemonics."""
    from .similar_matches import _aligned_prints, literal_variants, relocations
    from .twins import _declared, apply, rom_renames

    source = donor['matched_source'] or ''
    if not source:
        return []
    if [(o, t) for o, t, _ in relocations(donor)] == [(o, t) for o, t, _ in relocations(target)]:
        names = rom_renames(syms, source, pa, pb, donor['name'], target['name'])
    else:
        left, right = _aligned_prints(donor, target, pa, pb, ops_a, ops_b)
        names = rom_renames(syms, source, left, right, donor['name'], target['name'])
    if names is None:
        return []
    names = {**names, donor['name']: target['name']}
    words = set(re.findall(r'\b[A-Za-z_]\w*\b', source))
    names = {old: new for old, new in names.items() if old in words}
    prelude = _declared(header_a, header_b, names, donor['name'])
    moved = apply(source, names)
    out = [prelude + moved]
    out += [prelude + v for v in literal_variants(moved, donor, target, big)]
    return list(dict.fromkeys(out))


def candidates(net, proj, fn, header):
    """[(family, source)] from the closest matched look-alikes of `fn` in its own game."""
    if proj['arch'] != 'mips' or not fn['target'] or net.fork_slug(proj['slug']):
        return []
    from .. import similarity
    from .twins import rom_print

    out = []
    syms = net.symbols(proj['id'])
    store = net.project_store(proj['id'])
    big = bool(proj['big_endian'])
    for row, score in net.similar(proj, fn, top=DONORS * 2):
        if len(out) >= DONORS * 2 or row['status'] != 'matched' or score < FLOOR:
            continue
        donor = store.one("SELECT * FROM functions WHERE project_id=? AND name=? AND status='matched'", proj['id'], row['name'])
        if donor is None or not donor['matched_source'] or not donor['target']:
            continue
        sizes = sorted((len(donor['target']), len(fn['target'])))
        if sizes[0] < sizes[1] * RATIO:
            continue
        try:
            pa, pb = rom_print(donor, proj['arch'], big, syms.label), rom_print(fn, proj['arch'], big, syms.label)
            ops_a = similarity.mips_mnemonics(donor['target'], big, donor['vram'] or 0)
            ops_b = similarity.mips_mnemonics(fn['target'], big, fn['vram'] or 0)
            header_a = net._context(proj['id'], donor['unit'])['header']
            sources = transplant(donor, fn, syms, pa, pb, ops_a, ops_b, header_a, header, big)
        except Exception:  # noqa: BLE001 - a donor the alignment can't read costs only its candidate
            continue
        note = f'/* draft from the matched look-alike {donor["name"]} ({score:.0%} similar), tried by autopilot */\n'
        out += [('twin_draft', note + source) for source in sources[:2]]
    return out
