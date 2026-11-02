"""Read-only exact-byte peers and bounded instruction-pattern naming leads."""
from functools import lru_cache

from .. import arch as processors, scoring, similarity
from ..elfobj import FunctionObj
from ..network import IN_BUILD


@lru_cache(maxsize=1024)
def _ops(code, arch, big_endian, cpu):
    reader = processors.get(arch)
    if bool(big_endian) not in reader.capstone and cpu not in reader.variants:
        return ()
    obj = FunctionObj('', arch, bool(big_endian), code[:16384], cpu=cpu)
    return tuple(i.mnemonic for i in scoring.disassemble(obj) if not i.mnemonic.startswith('.'))[:similarity.MAX_OPS]


def listing(net, slug, function):
    proj = net.project_row(slug)
    fn = net._function(function, slug)
    code = fn['target']
    if not code:
        return {'exact': False, 'rows': [], 'total': 0, 'unavailable': 'Target bytes are not available for this function.'}
    query = ("SELECT f.id,f.name,f.status,f.target,p.slug,p.name AS project "
             "FROM functions f JOIN projects p ON p.id=f.project_id "
             f"WHERE f.project_id!=? AND p.arch=? AND p.big_endian=? AND f.{IN_BUILD} ")
    args = (proj['id'], proj['arch'], proj['big_endian'])
    exact = net.store.all(query+'AND f.target=? ORDER BY p.slug,f.name', *args, code)
    def row(r, score, identical):
        return {'id': r['id'], 'name': r['name'], 'slug': r['slug'], 'project': r['project'] or r['slug'],
                'status': r['status'], 'score': round(score, 4), 'identical': identical}
    if exact:
        return {'exact': True, 'rows': [row(r, 1, True) for r in exact[:100]], 'total': len(exact)}
    candidates = net.store.all(query+'AND length(f.target) BETWEEN ? AND ? '
                               'ORDER BY ABS(length(f.target)-?),p.slug,f.id LIMIT 400',
                               *args, max(1, int(len(code)*.6)), int(len(code)/.6), len(code))
    cpu = getattr(net.console(proj), 'cpu_variant', '') or ''
    target = _ops(code, proj['arch'], proj['big_endian'], cpu)
    by_id, ops = {}, {}
    for r in candidates:
        other = net.project_row(r['slug'])
        other_cpu = getattr(net.console(other), 'cpu_variant', '') or ''
        if other_cpu != cpu:
            continue
        key = str(r['id'])
        by_id[key] = r
        ops[key] = _ops(r['target'], proj['arch'], proj['big_endian'], cpu)
    ranked = similarity.rank(target, ops, top=12, floor=.6)
    return {'exact': False, 'rows': [row(by_id[k], score, False) for k, score in ranked],
            'total': len(ranked)}
