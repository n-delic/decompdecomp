"""Incremental identifier inventory: load source only when that source changed."""
import hashlib
import json
import re
import sqlite3
import zlib

from .. import payload_storage


def inventory(net, proj, progress=None):
    store=net.project_store(proj['id'])
    cache=net.__dict__.setdefault('_rename_source_words',{}).setdefault(proj['id'],{})
    cached_words={saved[0]:saved[1] for saved in cache.values()}
    # Capture metadata and only changed compressed bodies in one snapshot. Native
    # per-function reads yield the GIL on every row, even with a warm token cache.
    batches=store.all("""WITH chosen AS MATERIALIZED (
        SELECT id,name,unit,vram,matched_source AS storage FROM main.functions
        WHERE project_id=? AND status='matched'),
        refs AS (SELECT DISTINCT lower(hex(r.storage)) AS hash FROM chosen r
            WHERE typeof(r.storage)='blob'
            AND lower(hex(r.storage)) NOT IN (SELECT value FROM json_each(?))),
        packets AS (
        SELECT id AS position,'' AS hash,json_object('kind','function','id',id,'name',name,
            'unit',unit,'vram',CASE WHEN typeof(vram)='real' THEN printf('%!.17g',vram) ELSE vram END,
            'real_vram',typeof(vram)='real','packed',typeof(storage)='blob',
            'storage',CASE WHEN typeof(storage)='blob' THEN lower(hex(storage)) ELSE storage END) AS record FROM chosen
        UNION ALL SELECT NULL,r.hash,json_object('kind','payload','hash',r.hash,
            'payload',CASE WHEN p.hash IS NOT NULL THEN hex(p.payload) END)
        FROM refs r LEFT JOIN main.text_payloads p ON p.hash=r.hash),
        numbered AS (SELECT record,row_number() OVER (ORDER BY position,hash) AS ordinal FROM packets)
        SELECT json_group_array(json(record)) AS batch FROM numbered
        GROUP BY (ordinal-1)/128 ORDER BY min(ordinal)""",
        proj['id'],json.dumps([key.hex() for key in cached_words]))
    rows=[];decoded={};pages=len(batches)
    while batches:
        if progress:progress({'phase':'Reading matched sources','done':pages-len(batches),'total':pages})
        for r in json.loads(batches.pop()['batch']):
            if r.pop('kind')=='payload':
                blob=bytes.fromhex(r['payload']) if r['payload'] is not None else None
                try:decoded[r['hash']]=payload_storage.unpack(r['hash'],blob)
                except (ValueError,UnicodeError,zlib.error) as error:
                    raise sqlite3.OperationalError('user-defined function raised exception') from error
            else:rows.append(r)
    rows.sort(key=lambda r:r['id'])
    result=[];alive=set()
    for i,r in enumerate(rows):
        if progress and i%32==0:progress({"phase":"Finding affected matches","done":i,"total":len(rows)})
        alive.add(r['id']);raw=r['storage'] or ''
        key=bytes.fromhex(raw) if r['packed'] else hashlib.sha256(raw.encode()).digest()
        saved=cache.get(r['id'])
        if saved is None or saved[0]!=key:
            if key in cached_words:words=cached_words[key]
            else:
                source=decoded[raw] if r['packed'] else raw
                words=frozenset(re.findall(r'[A-Za-z_]\w*',source))
                cached_words[key]=words
            saved=(key,words)
            cache[r['id']]=saved
        if r['real_vram']:r['vram']=float(r['vram'])
        result.append(({k:r[k] for k in ('id','name','unit','vram')},saved[1]))
    for fid in set(cache)-alive:del cache[fid]
    return result
