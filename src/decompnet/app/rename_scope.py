"""Incremental identifier inventory: load source only when that source changed."""
import hashlib
import re


def inventory(net, proj, progress=None):
    store=net.project_store(proj['id'])
    cache=net.__dict__.setdefault('_rename_source_words',{}).setdefault(proj['id'],{})
    # Explicit main.functions reads physical storage: compressed text is a small
    # immutable digest, legacy short text remains TEXT. Never inflate other columns.
    rows=store.all("SELECT id,name,unit,vram,matched_source AS storage FROM main.functions WHERE project_id=? AND status='matched'",proj['id'])
    result=[];alive=set()
    for i,r in enumerate(rows):
        if progress and i%32==0:progress({"phase":"Finding affected matches","done":i,"total":len(rows)})
        alive.add(r['id']);raw=r['storage'] or ''
        key=raw if isinstance(raw,bytes) else hashlib.sha256(raw.encode()).digest()
        saved=cache.get(r['id'])
        if saved is None or saved[0]!=key:
            source=(store.one('SELECT matched_source FROM functions WHERE id=?',r['id'])['matched_source'] or '') if isinstance(raw,bytes) else raw
            saved=(key,frozenset(re.findall(r'[A-Za-z_]\w*',source)))
            cache[r['id']]=saved
        result.append(({k:r[k] for k in ('id','name','unit','vram')},saved[1]))
    for fid in set(cache)-alive:del cache[fid]
    return result
