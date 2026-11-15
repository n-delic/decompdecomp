"""Reuse current exact callers' scalar prototypes as local hypotheses, never shared-header edits."""
import re

from decomprepair.integer_repair_support import IDENT, INTEGER, edit, view
from decomprepair.repair_preflight import _PROTO

from .. import match_bundle
from ..project import compiler_for

FAMILY = 'verified_caller_prototype'
LIMIT = 6


def prototypes(source):
    clean=source
    if re.search(r'^\s*#|\b(?:static|asm|__asm__)\b',clean,re.M):return {}
    from ..forks.csource import _blank
    clean=_blank(clean);found={}
    for hit in _PROTO.finditer(clean):
        prefix=re.fullmatch(rf'\s*(?:extern\s+)?({INTEGER}|void)\s+{re.escape(hit[1])}\s*\(',hit[0][:hit[0].find('(')+1])
        if not prefix:continue
        params=[]
        for param in hit[2].split(','):
            m=re.fullmatch(rf'\s*({INTEGER}|void)(?:\s+{IDENT})?\s*',param)
            if not m:break
            params.append(' '.join(m[1].split()))
        else:
            canonical=' '.join(prefix[1].split())+' '+hit[1]+'('+', '.join(params)+');'
            found.setdefault(hit[1],[]).append((hit,canonical))
    return {n:v[0] for n,v in found.items() if len(v)==1}


def apply(source, name, donors):
    part=view(source,name)
    if not part:return []
    choices={}
    for donor in donors:
        for callee,(_,proto) in prototypes(donor).items():choices.setdefault(callee,set()).add(proto)
    out=[]
    for callee,(hit,current) in prototypes(source).items():
        pool=choices.get(callee,set())
        if callee==name or len(pool)!=1 or current in pool:continue
        if not re.search(r'\b'+re.escape(callee)+r'\s*\(',part.body):continue
        out.append((FAMILY,edit(source,[(hit.start(),hit.end(),'\n'+next(iter(pool)))])))
    return out[:LIMIT]


def variants(net, proj, fn, source):
    """Bounded donors in this game/profile with unchanged recorded exact inputs only."""
    if not net.is_rom(proj) or not net.cfg(proj).get('compiler'):return []
    names=list(prototypes(source))[:8]
    if not names:return []
    settings=compiler_for(net.cfg(proj),fn['vram'], fn)
    rows=net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='matched' AND id!=? AND ("+
        ' OR '.join('instr(matched_source,?)>0' for _ in names)+') ORDER BY id DESC LIMIT 24',
        proj['id'],fn['id'],*names)
    donors=[]
    for row in rows:
        if compiler_for(net.cfg(proj),row['vram'], row)!=settings:continue
        evidence=match_bundle.status(net,row)
        if evidence and not evidence['changed_inputs'] and not evidence.get('regression'):
            donors.append(evidence['source'])
    return apply(source,fn['name'],donors)


def signature(net, proj, source=''):
    """Only relevant exact donors/header revisions invalidate an exhausted recipient frontier."""
    import hashlib
    names=list(prototypes(source))[:8]
    if not names or not net.project_store(proj['id']).one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):return 'no-exact-donors'
    rows=net.project_store(proj['id']).all("SELECT v.function_id,v.details,f.unit FROM verified_matches v JOIN functions f ON f.id=v.function_id WHERE f.project_id=? AND f.status='matched' AND ("+
        ' OR '.join('instr(f.matched_source,?)>0' for _ in names)+') ORDER BY f.id DESC LIMIT 24',proj['id'],*names)
    units={r['unit'] for r in rows}
    contexts=[(unit,dict(net._context(proj['id'],unit) or {}).get('version')) for unit in sorted(units,key=str)]
    return hashlib.sha256(repr(([tuple(r) for r in rows],contexts)).encode()).hexdigest()
