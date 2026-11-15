"""Read-only exact-byte peers and bounded instruction-pattern naming leads."""
import json
from functools import lru_cache

from .. import arch as processors
from .. import scoring, similarity
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
    query = ("SELECT f.id,f.name,f.status,length(f.target) AS size,p.slug,p.name AS project "
             "FROM functions f JOIN projects p ON p.id=f.project_id "
             f"WHERE f.project_id!=? AND p.arch=? AND p.big_endian=? AND f.{IN_BUILD} ")
    args = (proj['id'], proj['arch'], proj['big_endian'])
    exact = [r for store,_ in net.project_stores() for r in store.all(query+'AND f.target=? ORDER BY p.slug,f.name',*args,code)]
    exact.sort(key=lambda r:(r['slug'],r['name']))
    def render(r, score, identical):
        return {'id': r['id'], 'name': r['name'], 'slug': r['slug'], 'project': r['project'] or r['slug'],
                'status': r['status'], 'score': round(score, 4), 'identical': identical}
    if exact:
        return {'exact': True, 'rows': [render(r, 1, True) for r in exact[:100]], 'total': len(exact)}
    choices = [(store,r) for store,_ in net.project_stores() for r in store.all(
        query+'AND length(f.target) BETWEEN ? AND ? ORDER BY ABS(length(f.target)-?),p.slug,f.id LIMIT 400',
        *args,max(1,int(len(code)*.6)),int(len(code)/.6),len(code))]
    choices.sort(key=lambda item:(abs(item[1]['size']-len(code)),item[1]['slug'],item[1]['id']))
    groups = {}
    for store,row in choices[:400]:groups.setdefault(store,[]).append(row['id'])
    targets = {r['id']:r['target'] for store,ids in groups.items() for r in store.all(
        'SELECT id,target FROM functions WHERE id IN (SELECT value FROM json_each(?))',json.dumps(ids))}
    candidates = [dict(row) | {'target':targets[row['id']]} for _,row in choices[:400]]
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
    return {'exact': False, 'rows': [render(by_id[k], score, False) for k, score in ranked],
            'total': len(ranked)}


def name_hints(net, slug, function):
    """Keep SDK provenance and normalized-code leads alongside raw-byte peers."""
    out = []
    for hint in net.twins.suggestions(slug, function, limit=100):
        hint = dict(hint)
        if hint.get('function_id'):
            store = net.project_store(hint['source_slug']) if hint.get('source_slug') else net.function_store(hint['function_id'])
            row = store.one('SELECT p.slug,f.name FROM functions f JOIN projects p ON p.id=f.project_id '
                                f'WHERE f.id=? AND f.{IN_BUILD}', hint['function_id']) if store is not None else None
            if row:
                hint.update(source_slug=row['slug'], source_function=row['name'])
        out.append(hint)
    return out
