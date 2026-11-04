"""Read-only library identities shared by the function list and automatic agent queue."""

from collections import defaultdict

from .sdk_library import allowed


def identify(net, proj, recorded):
    """Full-size, unambiguous SDK signatures only; never guess from a familiar name.

    Both ROM and imported projects use the target fingerprints cached by Twins.
    Nothing is written to project settings or counted as matching C.
    """
    version = (net.store.read_version('fingerprints', proj['id']), net.store.read_version('sdk_names'),
               repr(net.cfg(proj).get('sdk_libraries')))
    discovered = net.store.read_cache.get(('sdk-identities', proj['id']), version,
                                         lambda: _identify(net, proj), ttl=10 if net.cfg(proj).get('kind') == 'fork' else None)
    return dict(recorded) | discovered


def _identify(net, proj):
    recorded = {}
    from .shared_reuse import machine
    processor, big = machine(net, proj)
    signatures = net.store.all("SELECT corpus,name,fingerprint,size FROM sdk_signatures WHERE arch=? AND big_endian=?",
                               processor, big)
    if not signatures:
        return recorded
    by_key = defaultdict(list)
    for row in signatures:
        if row["size"] >= 32 and allowed(net, proj, row["corpus"]):
            by_key[row["fingerprint"]].append(row)
    if not by_key:
        return recorded
    out = dict(recorded)
    for fid, target in net.twins.prints(proj).items():
        hints = [r for r in by_key.get(target.key, []) if r["size"] == target.size]
        names = {r["name"] for r in hints}
        if not target.asm and target.size >= 32 and len(names) == 1:
            out[fid] = {"names": sorted(names), "corpora": sorted({r["corpus"] for r in hints}),
                        "evidence": "exact relocation-normalized SDK signature"}
    return out
