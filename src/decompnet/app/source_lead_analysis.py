"""Local source leads and measured differences. Ranking is evidence, never an identity or byte verdict."""
import hashlib
import json
import re
from collections import Counter

from ..forks.csource import _blank
from ..network import NetworkError
from ..project import compiler_for

PREFIX='source-analysis-v1:'


def destination(net, proj, fn):
    from .permuter_inputs import build_state
    cfg=json.loads(proj['config'])
    if getattr(net,'fork_slug',lambda slug:None)(proj['slug']):
        from ..candidate_scope import key
        return key(net,proj,fn)
    context=net._context(proj['id'],fn['unit']) or {'header':''}
    symbols=[tuple(r) for r in net.project_store(proj['id']).all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',proj['id'])]
    return hashlib.sha256(repr((compiler_for(cfg,fn['vram'], fn) if cfg.get('compiler') else {},cfg.get('gp'),proj['arch'],
                               fn['target'],fn['relocs'],fn['size'],fn['vram'],context['header'],symbols,build_state(proj))).encode()).hexdigest()


def parameters(source, name):
    match=re.search(r'\b'+re.escape(name)+r'\s*\(',_blank(source))
    if not match:return None
    clean=_blank(source);start=match.end();depth=1;count=0
    for i in range(start,len(clean)):
        if clean[i]=='(':depth+=1
        if clean[i]==')':depth-=1
        if not depth:
            text=clean[start:i].strip()
            return None if '...' in text or text=='' else 0 if text=='void' else count+1
        if clean[i]==',' and depth==1:count+=1
    return None


def features(lines, arch):
    calls=[];branches=[];offsets=set();immediates=set();exits=0
    for text in lines:
        text=text.strip();op=text.split()[0] if text else ''
        if op in {'jal','jalr','bal','bl','blx','call','callq'}:calls.append(text.split(None,1)[-1])
        if (arch=='mips' and op.startswith('b') and op not in {'bal','break'}) or op in {'beq','bne','bgt','blt','je','jne','jg','jl'}:branches.append(op)
        if op in {'addiu','ori','andi','slti','sltiu','cmp'}:
            value=re.search(r',\s*(-?0x[0-9a-fA-F]+|-?\d+)\s*$',text)
            if value:immediates.add((op,int(value[1],0)))
        exits+=bool(re.fullmatch(r'(?:jr\s+\$?ra|ret[q]?)',text))
        if arch=='mips':
            for match in re.finditer(r'(-?0x[0-9a-fA-F]+|-?\d+)\(\$?(?!sp\b|fp\b)[a-z]\w*\)',text):
                offsets.add(int(match[1],0))
    return {'calls':calls,'branches':branches,'exits':exits,'offsets':sorted(offsets),'immediates':sorted(immediates)}


def compare(rows, arch, source='', name=''):
    valid=[r for r in (rows or []) if isinstance(r,(tuple,list)) and len(r)==3 and all(isinstance(c,str) for c in r)]
    target=features([r[1] for r in valid if r[1]],arch)
    candidate=features([r[2] for r in valid if r[2]],arch)
    call_delta={k:v for k,v in (Counter(target['calls'])-Counter(candidate['calls'])).items()}
    extra_calls=dict(Counter(candidate['calls'])-Counter(target['calls']))
    markers={r[0] for r in valid if r[0]!=' '}
    register_only=bool(markers) and markers=={'r'}
    # Empty or fully inlined call sets supply no positive identity evidence.
    score=0
    if target['calls']:
        shared=sum((Counter(target['calls'])&Counter(candidate['calls'])).values())
        score+=round(60*shared/max(len(target['calls']),len(candidate['calls']),1))
    if target['offsets']:
        score+=round(30*len(set(target['offsets'])&set(candidate['offsets']))/len(set(target['offsets'])|set(candidate['offsets'])))
    if valid and target['branches']==candidate['branches'] and target['exits']==candidate['exits']:score+=10
    return {'target':target,'candidate':candidate,'missing_calls':call_delta,'extra_calls':extra_calls,
            'source_parameters':parameters(source,name) if name else None,'support':score,
            'register_only':register_only,'rows':[list(r) for r in valid[:160]],
            'note':'Call/branch/exit differences are in compiled code; inlining, optimization or ABI differences may explain them. They are not automatic retail-source edits.'}


def key(scope, source, header, family, profile):
    return PREFIX+hashlib.sha256(repr((scope,source,header,family,profile)).encode()).hexdigest()


def rejected(net, fn, scope, source, header, lead, profile):
    row=net.project_store(fn['project_id']).one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?',fn['id'],key(scope,source,header,lead.get('family','original-context'),profile))
    return bool(row and json.loads(row['feedback']).get('rejected'))


def profile(net, proj, fn):
    if getattr(net,'fork_slug',lambda slug:None)(proj['slug']):
        return {'build_owned':True,'scope':destination(net,proj,fn)}
    return compiler_for(json.loads(proj['config']),fn['vram'], fn)


def record(net, proj, fn, source, header, score, rows, lead=None, elapsed_ms=0, error='', scope=None, source_locations=None):
    lead=lead or {};family=lead.get('family','original-context')
    scope=scope or destination(net,proj,fn);settings=profile(net,proj,fn)
    signature=key(scope,source,header,family,settings)
    comparison=compare(rows,proj['arch'],source,fn['name'])
    from .matching_investigations import hints as investigation_hints
    from .source_branch_review import review
    from .source_context_diagnostics import hints as context_hints
    from .source_context_diagnostics import route
    from .source_next_steps import recommendations
    comparison['control_flow_review']=review(source,rows,proj['arch'],source_locations)
    comparison['context_route']=route(fn['asm'] or '',rows or [],proj['arch'])
    comparison['next_steps']=context_hints(source,comparison,fn['asm'] or '',rows or [],proj['arch'],source_locations)+investigation_hints(rows,proj['arch'])+recommendations(source,dict(fn).get('best_source') or '',comparison)
    from .source_storage import object_hints, scope_hints, typed_object_hints
    comparison['next_steps']+=scope_hints(dict(fn).get('best_source') or '',source,header,fn['name'])+object_hints(rows,header)+typed_object_hints(rows,source,header)
    if proj['arch']=='mips':
        from .source_storage import wide_argument_hints
        comparison['next_steps']+=wide_argument_hints(fn['asm'] or '',header)
    if proj['arch']=='mips':
        from ..arch.mips_runtime import incoming_arguments
        comparison['entry']=incoming_arguments(fn['asm'] or '')
    if hasattr(net,'reader'):
        from .source_context_diagnostics import strings
        if hasattr(net,'symbols'):
            comparison['next_steps']+=strings(source,fn['asm'] or '',net.reader(proj,fn),net.symbols(proj['id']).resolve)
        from .switch_tables import hints, read
        comparison['next_steps']+=hints(source,fn['name'],read(net,proj,fn),header)
    from .. import arch as arches
    reader=arches.get(proj['arch']).source_recovery_hints
    if reader:comparison['next_steps']+=reader(fn['asm'] or '',rows or [])
    private=arches.get(proj['arch']).source_private_abi
    if private:
        classification=private(fn['asm'] or '',rows or [])
        if classification:
            comparison['private_abi']=classification
            comparison['next_steps'].append(classification['message'])
    baseline=fn['best_score'];improved=score is not None and (baseline is None or score<baseline)
    item={'scope':scope,'family':family,'profile':settings,'lead':lead,'score':score,'baseline':baseline,
          'exact':score==0,'improved':improved,'error':error[:2000], 'elapsed_ms':max(0,int(elapsed_ms)),
          'baseline_source_sha256':hashlib.sha256((dict(fn).get('best_source') or '').encode()).hexdigest(),
          'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'header_sha256':hashlib.sha256(header.encode()).hexdigest(),
          'comparison':comparison}
    with net.project_store(proj['id']).tx() as db:
        previous=db.execute('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?',(fn['id'],signature)).fetchone()
        if previous:
            old=json.loads(previous['feedback'] or '{}')
            old_comparison=old.setdefault('comparison',{})
            old_comparison['next_steps']=list(dict.fromkeys(old_comparison.get('next_steps',[])+comparison['next_steps']))
            db.execute('UPDATE reuse_trials SET feedback=? WHERE function_id=? AND signature=?',(json.dumps(old),fn['id'],signature))
            return
        db.execute('INSERT INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (fn['id'],signature,str(score),'source lead and measured compiler evidence',json.dumps(item)))
    if hasattr(net,'cfg') and hasattr(net,'fork_slug'):
        from . import adaptive_repairs
        adaptive_repairs.record(net,adaptive_repairs.profile(net,proj,fn),'register' if comparison['register_only'] else 'source',
                                family,baseline,score,elapsed_ms,origin='local source preparation')


def listing(net, proj):
    rows=net.project_store(proj['id']).all('SELECT r.signature,r.feedback,f.name FROM reuse_trials r JOIN functions f ON f.id=r.function_id WHERE f.project_id=? AND r.signature LIKE ? ORDER BY r.rowid DESC LIMIT 500',proj['id'],PREFIX+'%')
    leads=[];groups={}
    for row in rows:
        item=json.loads(row['feedback']);family=item['family'];profile=item['profile']
        group=groups.setdefault((family,json.dumps(profile,sort_keys=True)),{'family':family,'profile':profile,'tested':0,'improved':0,'matched_functions':set(),'elapsed_ms':0})
        group['tested']+=1;group['improved']+=int(item['improved']);group['elapsed_ms']+=item['elapsed_ms']
        if item['exact']:group['matched_functions'].add(row['name'])
        leads.append({'id':row['signature'],'function':row['name'],**item})
    totals=[]
    for group in groups.values():
        group['matched']=len(group.pop('matched_functions'));totals.append(group)
    leads.sort(key=lambda r:(bool(r.get('rejected')),not r['exact'],-r['comparison']['support'],r['score'] if r['score'] is not None else float('inf')))
    return {'leads':leads[:40],'families':totals,'scope':'This project only; latest 500 distinct source/header/profile trials. Exact trials count distinct functions within each family/profile and may be unpromoted; they are not coverage and are not additive across families. Cached repeats are excluded; these are observations, not causal success rates.'}


def reject(net, proj, signature, reason):
    reason=str(reason).strip()
    if not reason:raise NetworkError('Give a reason for rejecting this source lead.')
    with net.project_store(proj['id']).tx() as db:
        row=db.execute('SELECT r.* FROM reuse_trials r JOIN functions f ON f.id=r.function_id WHERE f.project_id=? AND r.signature=? AND r.signature LIKE ?',
                       (proj['id'],signature,PREFIX+'%')).fetchone()
        if not row:raise NetworkError('This source lead is no longer available. Refresh its evidence.')
        item=json.loads(row['feedback']);item['rejected']=reason[:1000]
        db.execute('UPDATE reuse_trials SET feedback=? WHERE function_id=? AND signature=?',(json.dumps(item),row['function_id'],signature))
    return {'rejected':True}


def brief(net, proj, fn):
    lines=[]
    if proj['arch']=='mips':
        from ..arch.mips_runtime import incoming_arguments
        abi=incoming_arguments(fn['asm'] or '')
        if abi['mixed']:
            lines.append('Incoming float/integer entry lead: '+', '.join(abi['floating']+abi['integer'])+
                         '. An exposed ordinary-C signature may reserve different argument slots. Inspect original static-helper/caller context before broad permutation; no ABI is inferred or rewritten.')
            if hasattr(net,'xrefs'):
                callers=sorted(net.xrefs(proj)['called_by'].get(fn['vram'],()))[:4]
                for address in callers:
                    row=net.project_store(proj['id']).one('SELECT name,asm FROM functions WHERE project_id=? AND vram=?',proj['id'],address)
                    if row:
                        code=(row['asm'] or '').splitlines();at=next((i for i,line in enumerate(code) if re.search(r'\bjal\s+'+re.escape(fn['name'])+r'\b',line)),None)
                        lines.append('Caller '+row['name']+(':\n'+'\n'.join(code[max(0,at-3):at+2]) if at is not None else ' (inspect its call-site argument setup).'))
    if proj['arch']=='mips' and hasattr(net,'xrefs'):
        from ..arch.mips_runtime import specialized_argument_setup
        for address in sorted(net.xrefs(proj)['called_by'].get(fn['vram'],()))[:8]:
            caller=net.project_store(proj['id']).one('SELECT name,asm FROM functions WHERE project_id=? AND vram=?',proj['id'],address)
            evidence=specialized_argument_setup(fn['asm'] or '',caller['asm'] or '',fn['name']) if caller else None
            if evidence:
                lines.append('Specialized internal argument lead: '+', '.join(evidence['registers'])+
                             ' read before definition in this helper and set by '+caller['name']+':\n'+'\n'.join(evidence['setup']+[evidence['call']])+
                             '\nInspect the original same-unit helper/caller before broad body permutation. This does not assign registers or change a prototype.')
                break
    scope=destination(net,proj,fn)
    misses=[];next_steps=[];ineffective=[]
    for row in net.project_store(proj['id']).all('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature LIKE ? ORDER BY rowid DESC LIMIT 80',fn['id'],PREFIX+'%'):
        item=json.loads(row['feedback'])
        if item['scope']==scope and not item['improved']:
            next_steps.extend(item['comparison'].get('next_steps',[]))
            if item.get('baseline_source_sha256')==hashlib.sha256((dict(fn).get('best_source') or '').encode()).hexdigest():
                ineffective.append(item)
        if (item['scope']==scope and item.get('baseline_source_sha256')==hashlib.sha256((dict(fn).get('best_source') or '').encode()).hexdigest()
                and item['comparison']['register_only'] and not item['improved']):misses.append(item)
    lines.extend(list(dict.fromkeys(next_steps))[:3])
    if ineffective:
        examples=list(dict.fromkeys(f"{item['family']} at {item['profile'].get('toolchain','build-owned')} {' '.join(item['profile'].get('flags',[]))}: {item.get('score') if item.get('score') is not None else 'compile/link failure'}" for item in ineffective))
        lines.append('Already measured without improvement for this saved source/build scope (changed inputs remain eligible): '+ '; '.join(examples[:4])+'. Try a different context or hypothesis before repeating these probes.')
    if len(misses)>=3:
        lines.append(f'{len(misses)} distinct prepared source/context/profile probes retained register-only misses for these inputs. Try declaration lifetimes, original record/prototype context or helper calling convention before equivalent expression rewrites. All mutation families remain available.')
    return '\n'.join(lines)


def rank_pairs(net, proj, fn, pairs, families):
    """Use local measured family yield after 32 trials; interleave the original order for exploration."""
    from itertools import zip_longest
    own=compiler_for(json.loads(proj['config']),fn['vram'], fn)
    evidence=listing(net,proj)
    groups=[g for g in evidence['families'] if g['profile']==own and g['tested']>=32]
    weights={g['family']:(g['improved']+3*g['matched']+1)/(g['tested']+32) for g in groups}
    ranked=sorted(pairs,key=lambda pair:-weights.get(families.get(pair,'original-context'),1/32))
    interleaved=list(dict.fromkeys(pair for both in zip_longest(pairs,ranked) for pair in both if pair is not None))
    # Identity evidence is specific to a compiled source/header/profile. Preserve
    # unexplored leads, but stop a historically wrong mapping taking first place.
    scope=destination(net,proj,fn)
    measured={(e['source_sha256'],e['header_sha256']):e['comparison']
              for e in reversed(evidence.get('leads',[])) if e['function']==fn['name'] and e['scope']==scope and e['profile']==own}
    from .source_context_diagnostics import identity
    def priority(pair):
        hashes=tuple(hashlib.sha256(text.encode()).hexdigest() for text in pair)
        comparison=measured.get(hashes)
        return (bool(identity(comparison)), -comparison.get('support',0)) if comparison else (False,0)
    trials=net.project_store(proj['id']).all('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature LIKE ? ORDER BY rowid DESC LIMIT 512',fn['id'],PREFIX+'%')
    scores={}
    for row in trials:
        item=json.loads(row['feedback'] or '{}')
        if item.get('scope')!=scope or item.get('profile')!=own or item.get('rejected'):continue
        hashes=(item.get('source_sha256'),item.get('header_sha256'))
        scores.setdefault(hashes,item.get('score'))
    def measured_priority(pair):
        hashes=tuple(hashlib.sha256(text.encode()).hexdigest() for text in pair)
        score=scores.get(hashes)
        family=families.get(pair,'')
        # Known exact/better-supported pairs first, but new SDK contexts precede known failures.
        return (*priority(pair), 0 if score is not None else 1 if hashes not in scores else 2,
                score if score is not None else 0, 0 if family in ('cpu-private-source-unit','cpu-source-predecessors') else 1)
    return sorted(interleaved,key=measured_priority)
