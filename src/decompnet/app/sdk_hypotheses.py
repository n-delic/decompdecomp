"""Approximate local library leads. Never identities, automatic renames or acceptance verdicts."""
import json

from .. import scoring, similarity
from ..elfobj import extract_function
from .sdk_library import allowed


def shapes(member, corpus, prints):
    result=[]
    for name, fingerprint in prints.items():
        if not 128 <= fingerprint.size <= 65536:
            continue
        obj=extract_function(member,name)
        if obj is not None:
            ops=[line.mnemonic for line in scoring.disassemble(obj)]
            result.append((corpus,obj.arch,int(obj.big_endian),name,fingerprint.key,fingerprint.size,json.dumps(ops)))
    return result


def hints(net, proj, fn, limit=3):
    if fn['size'] is None or fn['size'] < 128:
        return []
    rows=net.store.all('SELECT * FROM sdk_shapes WHERE arch=? AND big_endian=? AND size BETWEEN ? AND ? '
                       'ORDER BY ABS(size-?),corpus,name LIMIT 96',proj['arch'],proj['big_endian'],
                       int(fn['size']*.7),int(fn['size']/ .7),fn['size'])
    rows=[r for r in rows if allowed(net,proj,r['corpus'])]
    if not rows:
        return []
    target=net._target_obj(fn,proj)
    ops=[line.mnemonic for line in scoring.disassemble(target)]
    candidates={str(i):json.loads(row['ops']) for i,row in enumerate(rows)}
    return [{'name':rows[int(i)]['name'],'game':rows[int(i)]['corpus'],'score':min(.999,round(score,3)),
             'kind':'similar library code; unverified'}
            for i,score in similarity.rank(ops,candidates,top=limit,floor=.82)]
