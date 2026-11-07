"""Local assembly evidence, kept distinct from C matches and never inferred from a name."""
import hashlib
import json
import os
import re
import time
from contextlib import nullcontext
from itertools import chain
from pathlib import Path

from .. import toolchains
from ..compiler import compile_assembly
from ..network import IN_BUILD, NetworkError
from ..project import compiler_config, compiler_for

SUFFIXES={'.s','.asm'}


def fingerprint(proj, fn):
    cfg=json.loads(proj['config']);profile=compiler_for(cfg,fn['vram'], fn)
    return hashlib.sha256(repr((cfg.get('rom',{}).get('sha1'),fn['vram'],fn['size'],
                               bytes(fn['target'] or b''),dict(fn).get('relocs'),cfg.get('gp'),cfg.get('big_endian'),profile)).encode()).hexdigest()


def verified(proj, fn):
    proof=json.loads(fn['meta'] or '{}').get('verified_assembly',{})
    if not isinstance(proof,dict):return False
    return (fn['status']=='asm' and proof.get('verified') is True and proof.get('fingerprint')==fingerprint(proj,fn))


def breakdown(net, proj):
    with getattr(net.project_store(proj['id']),'lock',nullcontext()):
        counts=net.project_store(proj['id']).one(f"SELECT SUM(status='matched') c,SUM(status='asm') a,SUM(status='open') o,SUM(size) bytes,SUM(CASE WHEN status='matched' THEN size ELSE 0 END) cbytes FROM functions WHERE project_id=? AND {IN_BUILD}",proj['id'])
        checked=0
        for row in net.project_store(proj['id']).all(f"SELECT vram,size,target,relocs,meta,status FROM functions WHERE project_id=? AND {IN_BUILD} AND status='asm' AND json_type(meta,'$.verified_assembly')='object'",proj['id']):
            checked+=int(verified(proj,row))
    return {'exact_c':counts['c'] or 0,'verified_source_assembly':checked,
            'other_assembly':(counts['a'] or 0)-checked,'open':counts['o'] or 0,
            'exact_c_bytes':counts['cbytes'] or 0,'total_bytes':counts['bytes'] or 0}


def scan(net, proj, cfg):
    """Discover label hypotheses in the selected root, never classify them without assembling."""
    from .source_preparation import MAX_FILES, Reader, mappings, symbol_aliases
    reader=Reader(cfg);aliases=symbol_aliases(cfg.get('aliases',''))
    aliases.update({s:d for d,s,_ in mappings(cfg.get('mappings',''))+mappings(cfg.get('assembly_mappings',''))})
    known={r['name'] for r in net.project_store(proj['id']).all("SELECT name FROM functions WHERE project_id=? AND status!='matched'",proj['id'])}
    leads=[];count=0
    # rglob does not follow directory symlinks; Reader also checks each resolved file.
    seen=set()
    for visited,path in enumerate(chain.from_iterable(root.rglob('*') for root in reader.roots),1):
        if visited>8192:break
        if path.suffix.lower() not in SUFFIXES or not path.is_file() or path.resolve() in seen:continue
        seen.add(path.resolve())
        count+=1
        if count>MAX_FILES:break
        text=reader.file(path)
        labels=re.findall(r'(?m)^\s*(?:LEAF|NESTED)\s*\(\s*(\w+)|^\s*\.(?:globl|global|ent)\s+(\w+)',text)
        for original in sorted({a or b for a,b in labels}):
            name=aliases.get(original,original)
            if name in known:leads.append({'function':name,'symbol':original,'file':os.path.relpath(path,reader.root),
                                         'status':'candidate; assembly and data not yet verified'})
    return leads[:MAX_FILES]


def run(net, proj, cfg, log, stop=lambda:False, budget=8, only=None, agent=None, retry=False):
    """Assemble configured source; an exact result records assembly proof, never C credit."""
    from .source_preparation import Reader, inputs, mappings, rename, symbol_aliases
    if not net.is_rom(proj) or net.fork_slug(proj['slug']):
        raise NetworkError('Supplied assembly verification currently needs a local ROM project; imported builds retain their own assembly rules.')
    reader=Reader(cfg);aliases=symbol_aliases(cfg.get('aliases',''))
    mappings_=mappings(cfg.get('assembly_mappings',''))
    aliases.update({s:d for d,s,_ in mappings(cfg.get('mappings',''))+mappings_})
    report={'tested':0,'verified':0,'cached':0,'errors':[]}
    if agent is None:agent,_=net.local_agent('autopilot')
    for dest,symbol,file in mappings_:
        if stop() or report['tested']>=budget:break
        if only is not None and dest not in only:continue
        fn=net._function(dest,proj['slug'])
        if fn['status']=='matched' or not net.permuter._hold(agent,fn,count_turn=False,seconds=120):continue
        failure_key=None
        try:
            proj=net.project_row(proj['slug']);fn=net._function(dest,proj['slug']);expected=inputs(net,proj,fn)
            source=reader.flatten(reader.root/file)
            prelude=reader.flatten(Path(cfg['assembly_header']).expanduser()) if cfg.get('assembly_header') else ''
            prelude='\n'.join('#define '+k+' '+v for k,v in reader.overrides.items())+'\n'+prelude
            source=rename(source,{**aliases,symbol:dest});prelude=rename(prelude,aliases)
            source_hash=hashlib.sha256(source.encode()).hexdigest();prelude_hash=hashlib.sha256(prelude.encode()).hexdigest()
            old=json.loads(fn['meta'] or '{}').get('verified_assembly',{})
            if verified(proj,fn) and old.get('source_sha256')==source_hash and old.get('prelude_sha256')==prelude_hash:
                report['cached']+=1;continue
            key='local-assembly-v2:'+hashlib.sha256(repr((fingerprint(proj,fn),source_hash,prelude_hash)).encode()).hexdigest()
            cached=net.project_store(proj['id']).one('SELECT reason FROM reuse_trials WHERE function_id=? AND signature=?',fn['id'],key)
            if cached and not retry:
                report['errors'].append(dest+': unchanged failed assembly trial skipped; use Verify mapped assembly to retry.');continue
            base=compiler_for(json.loads(proj['config']),fn['vram'], fn)
            if base.get('cmd') or not toolchains.known(base.get('toolchain','')) or toolchains.installed(base['toolchain']) is None:
                raise NetworkError('Install and select a supported compiler driver before verifying assembly.')
            profiles=[base]
            # Reorderable source assembly can need a different optimization level
            # from neighbouring C. One bounded alternative never changes C settings.
            if any(flag in ('-O2','-O3') for flag in base.get('flags',[])):
                profiles.append({**base,'flags':['-O1' if f in ('-O2','-O3') else f for f in base['flags']]})
            success=False;attempted=0;diagnostic='Assembly did not pass exact code/data checks.'
            for profile in profiles:
                if stop() or report['tested']>=budget:break
                attempted+=1;report['tested']+=1
                log(dest+': assembling and checking code/data with '+' '.join(profile.get('flags',[])))
                result=compile_assembly(compiler_config(Path(proj['root']),{'compiler':profile}),prelude,source)
                if not result.ok:diagnostic=result.log[-2000:];continue
                candidate,exact,problems=net._evaluate(fn,proj,result.obj)
                if candidate is not None and exact is True:success=True;break
                diagnostic='Assembly did not pass exact code/data checks: '+'; '.join(problems)
            if not success:
                if attempted<len(profiles):continue  # no negative cache for unfinished work
                failure_key=key;raise NetworkError(diagnostic)
            proof={'verified':True,'fingerprint':fingerprint(proj,fn),'source_file':file,'original_file_sha256':reader.hashes[(reader.root/file).resolve()],
                   'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                   'prelude_sha256':hashlib.sha256(prelude.encode()).hexdigest(),
                   'object_sha256':hashlib.sha256(result.obj).hexdigest(),'compiler':profile,'at':time.time()}
            with net.progress.judging(proj['slug']),net.progress._project_lock(proj['slug']),net.project_store(proj['id']).tx() as db:
                current=net._function(dest,proj['slug']);fresh=net.project_row(proj['slug'])
                claim=net.project_store(proj['id']).one('SELECT agent_id,expires_at FROM claims WHERE function_id=?',fn['id'])
                if inputs(net,fresh,current)!=expected or current['status'] not in ('open','asm') or not claim or claim['agent_id']!=agent.id or claim['expires_at']<=time.time():
                    raise NetworkError('Assembly inputs or claim changed; run verification again.')
                meta=json.loads(current['meta'] or '{}');meta['verified_assembly']=proof
                meta['asm_code']='Supplied source assembly passed exact linked code/data verification.'
                db.execute("UPDATE functions SET status='asm',meta=? WHERE id=?",(json.dumps(meta),fn['id']))
                net._event(db,proj['id'],agent.id,'assembly-verified',dest)
            report['verified']+=1;log(dest+': verified source assembly; no C coverage added.')
        except NetworkError as error:
            if failure_key:
                with net.project_store(proj['id']).tx() as db:
                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',(fn['id'],failure_key,'failed',str(error)[:2000]))
            report['errors'].append(dest+': '+str(error));log(report['errors'][-1])
        finally:net.permuter._let_go(agent,fn)
    return report


def detail(proj, fn):
    proof=json.loads(fn['meta'] or '{}').get('verified_assembly')
    return {**proof,'verified':verified(proj,fn)} if isinstance(proof,dict) else None
