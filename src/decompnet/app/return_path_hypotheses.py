"""Target-backed branching return hypotheses, always isolated diagnostics, never ordinary rewrites."""
import re

from .. import arch as architectures
from ..forks.csource import _blank, find
from . import integer_abi
from .equal_value_flow import macros_safe
from .integer_repair_support import IDENT, edit, locals_for, view
from .pattern_repairs import close

LIMIT = 4


def evidence(net,proj,fn, *, include_callers=True):
    if not getattr(net,'is_rom',lambda _:False)(proj) or net.fork_slug(proj['slug']):return None
    reader=architectures.get(proj['arch']).source_return_paths
    if not reader:return None
    asm=net._asm_text(fn,proj) if hasattr(net,'_asm_text') else fn['asm'] or ''
    proof=reader(asm)
    if not proof:return None
    references=[]
    if include_callers and hasattr(net,'xrefs'):
        addresses=sorted(net.xrefs(proj).get('called_by',{}).get(fn['vram'],()))[:8]
        for address in addresses:
            row=net.project_store(proj['id']).one('SELECT * FROM functions WHERE project_id=? AND vram=?',proj['id'],address)
            if not row:continue
            caller=net._asm_text(row,proj)
            sample=reader(asm,caller,fn['name'])
            if sample:references.append(dict(name=row['name'],uses=sample['caller_uses']))
    return dict(proof,callers=references)


def variants(source,name,header,arch,proof):
    """Invert a final signed guard; optionally give its real scalar a separate lexical lifetime."""
    if not proof or proof.get('truncated') or not proof.get('guarded_exits'):return []
    part=view(source,name)
    if not part or re.search(r'\b(?:return|goto|volatile|asm|__asm__)\b',part.body):return []
    start,_=find(source,name)
    signature=re.search(r'\bvoid\s+'+re.escape(name)+r'\s*\(',part.clean[start:part.opening])
    if not signature:return []
    if re.search(r'\b'+re.escape(name)+r'\s*\(',_blank(header+'\n'+source[:start]+source[part.end:]+part.body)):return []
    declarations={d['name']:d['type'] for d in locals_for(part)}
    pattern=rf'(?P<var>{IDENT})\s*=(?!=)(?P<expr>[^;{{}}]+);\s*if\s*\(\s*(?P=var)\s*(?P<cmp><=|>)\s*0\s*\)\s*{{'
    out=[]
    for hit in re.finditer(pattern,part.clean[part.opening+1:part.end-1]):
        offset=part.opening+1;begin=offset+hit.start();opening=offset+hit.end()-1
        finish=close(part.clean,opening,'{','}')
        if not finish or part.clean[finish:part.end-1].strip():continue
        var=hit['var'];typ=declarations.get(var)
        if re.search(r'\b'+re.escape(var)+r'\b',hit['expr']):continue
        if not macros_safe(source,header,source[begin:finish],{var}):continue
        if not typ or integer_abi.scalar(typ,header+'\n'+source,arch)!=(32,True):continue
        guard='> 0' if hit['cmp']=='<=' else '<= 0'
        if guard not in {p['condition'] for p in proof['guarded_exits']}:continue
        condition=re.search(r'\bif\s*\(',part.clean[begin:opening]);condition_start=begin+condition.start()
        # Preserve the existing block, assignment text, calls and physical lines. The opposite
        # exit returns its real tested scalar; fallthrough has no invented return value.
        statement=source[begin:condition_start]
        body=source[opening:finish]
        new=statement+f'if ({var} {guard}) return {var};\n'+body
        return_type=start+signature.start()
        base=edit(source,[(return_type,return_type+4,typ),(begin,finish,new)])
        out.append(('path_return',base))
        fresh='return_value'
        if re.search(r'\b'+fresh+r'\b',_blank(header+'\n'+source)):continue
        renames=[(m.start(),m.end(),fresh) for m in re.finditer(r'\b'+re.escape(var)+r'\b',_blank(new))
                 if not re.search(r'(?:\.|->)\s*$',_blank(new[:m.start()]))]
        scoped='{ '+typ+' '+fresh+';\n'+edit(new,renames)+'\n  }'
        out.append(('path_return+scalar_scope',edit(source,[(return_type,return_type+4,typ),(begin,finish,scoped)])))
        if len(out)>=LIMIT:break
    return out[:LIMIT]


def brief(net,proj,fn):
    proof=evidence(net,proj,fn)
    if not proof or not proof['guarded_exits']:return ''
    uses=[r['name']+': '+','.join(u['use'] for u in r['uses']) for r in proof['callers']]
    guards=', '.join('result '+r['condition'] for r in proof['guarded_exits'])
    return ('Next diagnostic experiment: target has a result-preserving exit ('+guards+'). Test a final scalar guard/partial-return hypothesis, then a separate lifetime for that scalar, even if the single edit scores worse. '
            'Known direct callers: '+('; '.join(uses) or 'not established')+'. No automatic interface change; other exits may leave scratch results. Indirect callers remain unproven.')
