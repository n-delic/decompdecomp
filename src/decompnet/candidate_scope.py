"""Build evidence local to a function, shared by cooldowns and persistent trial history."""
import hashlib
import json
import re
from pathlib import Path

from .candidate_work import current, remember
from .forks.project import ForkError
from .project import compiler_for


def digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return 'unavailable'


def dependencies(root, source):
    """Follow quoted source includes; unresolved/system includes conservatively watch include/."""
    pending=[root/source] if source else []
    seen={};fallback=False
    while pending and len(seen)<256:
        path=pending.pop()
        if path in seen:
            continue
        seen[path]=digest(path)
        try:
            text=path.read_text(errors='replace')
        except OSError:
            fallback=True
            continue
        for delimiter,name in re.findall(r'^\s*#\s*include\s*(["<])([^">]+)[">]',text,re.M):
            choices=[path.parent/name,root/'include'/name,root/name]
            found=next((p for p in choices if p.is_file() and p.resolve().is_relative_to(root.resolve())),None)
            if found is not None:
                pending.append(found)
            else:
                fallback=True
    if pending or fallback:
        seen.update((p,digest(p)) for p in (root/'include').rglob('*.h'))
    return sorted((str(p),value) for p,value in seen.items())


def callees(net, proj, fn, source, max_source_chars=None):
    """Only referenced matched helpers can supply new signature/write-footprint evidence."""
    store = net.project_store(proj['id'])
    from .forks.csource import _blank
    names=sorted(set(re.findall(r'\b([A-Za-z_]\w*)\s*\(',_blank(source))) -
                 {fn['name'],'if','for','while','switch','sizeof'})[:16]
    if not names:
        return []
    bound = ' AND length(matched_source)<=?' if max_source_chars is not None else ''
    args = (proj['id'],json.dumps(names)) + ((max_source_chars,) if max_source_chars is not None else ())
    return store.all("SELECT name,matched_source FROM functions WHERE project_id=? AND status='matched' "
                         'AND name IN (SELECT value FROM json_each(?))'+bound+' ORDER BY name',*args)


def batch_context(net, proj):
    """Shared read-only inputs for one bounded scheduling decision, never a judge verdict."""
    store = net.project_store(proj['id'])
    return {'project_id':proj['id'], 'store_id':id(store),
            'contexts':{}, 'versions':{kind:store.read_version(kind,proj['id'])
            for kind in ('scope_context','scope_symbols','matched_evidence')},
            'fork':bool(getattr(net,'fork_slug',lambda _:None)(proj['slug']))}


def _check_batch(shared, proj, store):
    if shared is not None and ('project_id' in shared or 'store_id' in shared):
        if shared.get('project_id') != proj['id'] or shared.get('store_id') != id(store):
            from .network import NetworkError
            raise NetworkError('Scheduling inputs belong to another project or database. Rebuild this scheduling batch.')


def _version(net, proj, kind, shared):
    store = net.project_store(proj['id'])
    if shared is not None and kind in shared.get('versions',{}):
        return shared['versions'][kind]
    return store.read_version(kind,proj['id'])


def context(net, proj, unit, shared=None):
    store = net.project_store(proj['id'])
    _check_batch(shared, proj, store)
    def load():
        if hasattr(store, 'read_cache'):
            return store.read_cache.get(('candidate-header', proj['id'], unit),
                (_version(net,proj,'scope_context',shared), getattr(net._context, '__func__', net._context)),
                lambda: net._context(proj['id'], unit))
        return net._context(proj['id'], unit)
    if shared is None:
        return load()
    if unit not in shared['contexts']:
        shared['contexts'][unit] = load()
    return shared['contexts'][unit]


def state(net,proj,fn,shared=None):
    store = net.project_store(proj['id'])
    _check_batch(shared, proj, store)
    cfg=json.loads(proj['config'])
    build={key:cfg.get(key) for key in ('kind','platform','big_endian','gp')}
    build['compiler']=compiler_for(cfg,fn['vram'], fn) if cfg.get('compiler') else None
    ctx=context(net,proj,fn['unit'],shared)
    text='\n'.join(str(fn[key] or '') for key in ('name','asm','best_source','seed','relocs'))
    names=set(re.findall(r'\b[A-Za-z_]\w*\b',text))
    addresses={int(x,16) for x in re.findall(r'\b0x([0-9a-fA-F]+)\b',text)}
    addresses.update(int(x,16) for x in re.findall(r'\b(?:D_|func_)([0-9a-fA-F]{8})\b',text))
    if fn['vram'] is not None:
        addresses.add(fn['vram'])
    def build_index():
        rows = [tuple(r) for r in store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
        by_name, by_address = {}, {}
        for index, row in enumerate(rows):
            by_name.setdefault(row[0], []).append(index)
            by_address.setdefault(row[1], []).append(index)
        return {'symbols': rows, 'by_name': by_name, 'by_address': by_address}
    lookup = shared.get('symbol_lookup') if shared is not None and 'versions' in shared else None
    if lookup is None:
        lookup = (store.read_cache.get(('candidate-symbols', proj['id']), _version(net,proj,'scope_symbols',shared), build_index)
                  if hasattr(store, 'read_cache') else build_index())
        if shared is not None and 'versions' in shared:
            shared['symbol_lookup']=lookup
    selected = {i for name in names for i in lookup['by_name'].get(name, ())}
    selected.update(i for address in addresses for i in lookup['by_address'].get(address, ()))
    symbols = [lookup['symbols'][i] for i in sorted(selected)]
    result=[build,proj['root'],proj['arch'],proj['big_endian'],ctx['header'] if ctx else None,symbols,fn['target'],fn['relocs'],fn['vram']]
    source = (fn['best_source'] or '')+'\n'+(fn['seed'] or '')
    def callee_hashes():
        return [(r['name'],hashlib.sha256((r['matched_source'] or '').encode()).hexdigest())
                for r in callees(net,proj,fn,source)]
    # A scheduling batch caches its complete result. Thousands of one-use callee
    # entries would evict that project result from the bounded shared cache and
    # force the next claim in the same batch to recompute every paused function.
    if hasattr(store, 'read_cache') and shared is None:
        evidence = store.read_cache.get(('candidate-callees', proj['id'], fn['name'], hashlib.sha256(source.encode()).hexdigest()),
            _version(net,proj,'matched_evidence',shared), callee_hashes)
    else:
        evidence = callee_hashes()
    result.append(evidence)
    fork=shared['fork'] if shared is not None and 'fork' in shared else getattr(net,'fork_slug',lambda _:None)(proj['slug'])
    if fork:
        try:
            _,fp,_=net.forks.get(proj['slug']);unit=fp.unit(fn['unit'])
            target=getattr(unit,'target',None)
            target=Path(target) if target else None
            if target is not None and not target.is_absolute():
                target=fp.root/target
            result += [digest(target) if target else None,repr(unit), dependencies(fp.root,unit.source),
                       [(name,digest(fp.root/name)) for name in ('Makefile','build.ninja','objdiff.json','configure.py')]]
        except (ForkError,OSError):
            result.append(('unavailable-project',digest(Path(proj['root'])/'objdiff.json')))
    return result


def key(net,proj,fn,shared=None):
    store = net.project_store(proj['id'])
    def build():return hashlib.sha256(repr(state(net,proj,fn,shared)).encode()).hexdigest()
    # Imported build files can change without a database revision: always inspect those files.
    if current() is None:return build()
    if shared is not None or getattr(net, 'fork_slug', lambda _: None)(proj['slug']):return build()
    if not hasattr(store, 'read_version'):return build()
    token = (id(net), id(store), store.read_version('inputs', proj['id']),
             hashlib.sha256(repr((dict(proj), dict(fn))).encode()).digest())
    return remember(('candidate-scope', token), build)
