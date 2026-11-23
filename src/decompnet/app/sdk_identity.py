"""Read-only library identities shared by the function list and automatic agent queue."""

from collections import defaultdict

from .maintenance import checkpoint


def identify(net, proj, recorded, *, include_asm=False, builtin=False):
    """Full-size, unambiguous SDK signatures only; never guess from a familiar name.

    `builtin`: the app's own tables count whatever libraries the project's Settings selected (the port asks this:
    whether a routine is libultra's doesn't depend on which libraries the user ticked for display. A project whose
    selection was saved before a table shipped, a 2.0D game's exact osDestroyThread, kept its own copy and spun on
    kernel lists the host doesn't keep).

    Both ROM and imported projects use the target fingerprints cached by Twins.
    Nothing is written to project settings or counted as matching C. Naming may
    include exact assembly identities; the default C-work queue still excludes them.
    """
    from .sdk_builtin import ensure
    ensure(net)  # the libraries the app ships, loaded before the index's version is read
    version = (net.project_store(proj['id']).read_version('fingerprints', proj['id']), net.store.read_version('sdk_names'),
               net.project_store(proj['id']).read_version('naming', proj['id']),
               repr(net.cfg(proj).get('sdk_libraries')), include_asm, builtin)
    discovered = net.project_store(proj['id']).read_cache.get(('sdk-identities', proj['id'], include_asm, builtin), version,
                                         lambda: _identify(net, proj, include_asm=include_asm, builtin=builtin), ttl=10 if net.cfg(proj).get('kind') == 'fork' else None)
    from .sdk_evidence import current
    return dict(recorded) | {fid: hint for fid, hint in discovered.items() if current(proj, hint)}


def naming_prints(net, proj):
    """Add ROM assembly targets only for naming; Twins intentionally omits them.

    Imported target prints already include assembly. Keep their normal readers
    and keep assembly out of the shared C-reuse fingerprint cache.
    """
    prints = dict(net.twins.prints(proj))
    if net.cfg(proj).get('kind') == 'fork':
        return prints
    from .twins import rom_print
    symbols = net.symbols(proj['id'])
    for row in net.project_store(proj['id']).all("SELECT id,target,relocs FROM functions WHERE project_id=? AND status='asm'", proj['id']):
        checkpoint()
        target = rom_print(row, proj['arch'] or 'mips', bool(net.cfg(proj).get('big_endian', True)), symbols.label)
        target.asm = 'hand-written assembly'
        prints[row['id']] = target
    return prints


def ambiguous(net, proj, *, include_asm=True, builtin=False):
    """{function id: its candidate library names} for functions whose full-size fingerprint several library routines
    share (identical bodies: osViGetCurrentFramebuffer and osViGetNextFramebuffer are the same 64 bytes). Naming
    refuses them (`identify`); the port may use one when its host answers every candidate the same way."""
    from .sdk_builtin import ensure
    ensure(net)
    version = (net.project_store(proj['id']).read_version('fingerprints', proj['id']), net.store.read_version('sdk_names'),
               repr(net.cfg(proj).get('sdk_libraries')), include_asm, builtin)
    return net.project_store(proj['id']).read_cache.get(('sdk-ambiguous', proj['id'], include_asm, builtin), version,
                                                       lambda: _identify(net, proj, include_asm=include_asm, collect={},
                                                                         builtin=builtin) or {})


def _identify(net, proj, *, include_asm=False, collect=None, builtin=False):
    recorded = {}
    from .shared_reuse import machine
    processor, big = machine(net, proj)
    def corpus_index():
        grouped = defaultdict(list)
        for row in net.store.all("SELECT corpus,name,fingerprint,size FROM sdk_signatures WHERE arch=? AND big_endian=? AND size>=32",
                                 processor, big):
            checkpoint()
            grouped[row['fingerprint']].append(dict(row))
        return dict(grouped)
    by_key = net.store.read_cache.get(('sdk-corpus-index', processor, big),
                                     net.store.read_version('sdk_names'), corpus_index)
    from .sdk_evidence import identities
    binary = identities(net, proj, include_asm=include_asm)
    selection = net.cfg(proj).get('sdk_libraries')
    selected = set(selection) if selection is not None else None
    if include_asm:
        from .source_assembly import detail
        for row in net.project_store(proj['id']).all("SELECT id,name,vram,rom_offset,size,target,relocs,meta,status FROM functions WHERE project_id=? AND status='asm' AND json_extract(meta,'$.verified_assembly.kind')='sdk'",proj['id']):
            proof=detail(proj,row)
            if proof and proof['verified'] and (proof.get('verification_method')=='builtin-sdk-digest' or selected is None or proof['corpus'] in selected):
                binary[row['id']]={'names':proof.get('names',[proof['name']]),'corpora':[proof['corpus']],
                                'evidence':proof['evidence'],'verified_assembly':True,
                                **({'sdk_file':proof['source_file'],'sdk_stamp':proof.get('source_stamp')} if proof.get('source_file') else {})}
    if not by_key:
        return {} if collect is not None else binary  # No local corpus: avoid reading target objects merely to discover there is no evidence.
    selection = net.cfg(proj).get('sdk_libraries')
    selected = set(selection) if selection is not None else None
    if builtin and selected is not None:
        from .sdk_builtin import corpora as shipped
        selected |= shipped(net)
    out, ambiguous = dict(recorded), set()
    for fid, target in (naming_prints(net, proj) if include_asm else net.twins.prints(proj)).items():
        checkpoint()
        hints = [r for r in by_key.get(target.key, []) if r["size"] == target.size
                 and (selected is None or r["corpus"] in selected)]
        if not hints and getattr(target, "alt_key", ""):  # (address pairs the split didn't record: twins.unrecorded_pairs)
            hints = [r for r in by_key.get(getattr(target, "alt_key", ""), []) if r["size"] == target.size
                     and (selected is None or r["corpus"] in selected)]
        names = {r["name"] for r in hints}
        if len(names) > 1:
            ambiguous.add(fid)
            if collect is not None and (include_asm or not target.asm) and target.size >= 32:
                collect[fid] = {"names": sorted(names), "corpora": sorted({r["corpus"] for r in hints}),
                                "fingerprint": target.key}
        if (include_asm or not target.asm) and target.size >= 32 and len(names) == 1:
            out[fid] = {"names": sorted(names), "corpora": sorted({r["corpus"] for r in hints}),
                        "fingerprint": target.key, "evidence": "exact relocation-normalized SDK signature"}
    if builtin and collect is None:
        _by_shape(net, proj, out, ambiguous)
    for fid, hint in binary.items():
        if hint.get('verified_assembly'):
            out[fid]=hint
            continue
        if fid in ambiguous:
            continue
        if fid in out and out[fid]['names'] != hint['names']:
            out.pop(fid)  # Conflicting full-size identities need review, not automatic naming.
        elif fid in out:
            out[fid] = hint | {'corpora': sorted(set(out[fid]['corpora']) | set(hint['corpora']))}
        else:
            out[fid] = hint
    return collect if collect is not None else out


def _by_shape(net, proj, out, ambiguous):
    """The console's routines told by structure (`Console.sdk_shape_identity`), for functions no fingerprint named: the
    build of libultra a game links can be one no table has, and the port needs the name to give the host's version."""
    console = net.console(proj)
    low, high = console.sdk_shape_sizes
    if not high:
        return
    big = bool(net.cfg(proj).get('big_endian', True))
    for row in net.project_store(proj['id']).all("SELECT id,target FROM functions WHERE project_id=? AND size BETWEEN ? AND ?",
                                                 proj['id'], low, high):
        if row['id'] in out or row['id'] in ambiguous or not row['target']:
            continue
        checkpoint()
        found = console.sdk_shape_identity(bytes(row['target']), big)
        if found:
            out[row['id']] = {'names': [found['name']], 'corpora': [found['corpus']], 'fingerprint': '',
                              'evidence': found['evidence']}


def display_name(hint, fallback):
    if not hint:return fallback
    names=hint.get('names',[])
    if len(names)==1 or hint.get('verified_assembly'):
        return ' / '.join(names) or fallback
    return fallback
