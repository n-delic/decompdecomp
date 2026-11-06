"""Operator-reviewed raw-tail recovery, staged before an atomic live project update."""
import hashlib
import json
import tempfile
from pathlib import Path

import yaml

from ..network import IN_BUILD, NetworkError
from ..rom import split
from ..rom.export import _symbol_lines
from ..rom.reviewed import extend_config
from ..rom.symbols import Symbol, SymbolTable


def _plans(net,proj,changes,image):
    if not isinstance(changes,list) or not 1<=len(changes)<=32:raise NetworkError('Review between one and 32 function extensions.')
    out=[];seen=set()
    for change in changes:
        name=change.get('function');end=change.get('end');reason=change.get('reason','')
        if name in seen or not isinstance(reason,str) or not reason.strip():raise NetworkError('Each function needs one extent and its review evidence.')
        seen.add(name);fn=net._function(name,proj['slug'])
        if type(end) is not int or fn['vram'] is None or not fn['vram']+fn['size']<end<=fn['vram']+0x10000 or end%4:
            raise NetworkError('The reviewed end must be an aligned extension of at most 64 KiB.')
        if fn['status']=='matched':raise NetworkError('A matched function cannot be extended.')
        if net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id']):raise NetworkError('Release the function claim before changing its extent.')
        start=fn['rom_offset'];size=end-fn['vram']
        if start is None or start<0 or start+size>len(image) or image[start:start+fn['size']]!=fn['target']:
            raise NetworkError('The stored prefix does not describe unchanged original image bytes.')
        peers=net.store.all(f'SELECT id,vram,rom_offset,size FROM functions WHERE project_id=? AND {IN_BUILD}',proj['id'])
        if any(p['id']!=fn['id'] and ((p['rom_offset'] is not None and p['rom_offset']<start+size and start<p['rom_offset']+p['size']) or
               (p['vram'] is not None and p['vram']<end and fn['vram']<p['vram']+p['size'])) for p in peers):
            raise NetworkError('The reviewed extent overlaps another function; use a reviewed join instead.')
        out.append({'row':dict(fn),'name':name,'vram':fn['vram'],'rom':start,'size':size,'reason':reason.strip()[:4000]})
    return out


def _stage(net,proj,plans,image,config,log):
    """Splat reads a private image/config with complete reviewed symbol sizes."""
    with tempfile.TemporaryDirectory(prefix='decomp-reviewed-') as folder:
        root=Path(folder);config=json.loads(json.dumps(config));options=config['options']
        options.update(base_path='.',target_path='image.z64',symbol_addrs_path=['symbol_addrs.txt'])
        config['sha1']=hashlib.sha1(image).hexdigest()
        (root/'image.z64').write_bytes(image)
        symbols=_symbol_lines(net,proj)
        for p in plans:
            symbols=[s for s in symbols if not s.startswith(p['name']+' = ')]
            symbols.append(f"{p['name']} = 0x{p['vram']:08X}; // type:func size:0x{p['size']:X}")
        (root/'symbol_addrs.txt').write_text('\n'.join(symbols)+'\n')
        (root/'reviewed.yaml').write_text(yaml.safe_dump(config,sort_keys=False))
        split._splat(['split','reviewed.yaml'],root,log,timeout=300)
        found={}
        for path in (root/'asm/nonmatchings').rglob('*.s'):
            fn=split.parse_function(path,path.parent.name,proj['big_endian'])
            if fn and any(fn.vram==p['vram'] for p in plans):found[fn.vram]=fn
        names={}
        for path in [root/'symbol_addrs.txt',root/'undefined_syms_auto.txt',root/'undefined_funcs_auto.txt']:
            if path.exists():names.update((m[1],int(m[2],16)) for m in split._SYMBOL_LINE.finditer(path.read_text()))
        for p in plans:
            fn=found.get(p['vram'])
            if fn is None or fn.rom!=p['rom'] or fn.size!=p['size']:
                raise NetworkError('Staged disassembly did not retain the exact reviewed extent; no project changes were made.')
            if b''.join(x.word.to_bytes(4,'big' if proj['big_endian'] else 'little') for x in fn.lines)!=image[p['rom']:p['rom']+p['size']]:
                raise NetworkError('Staged disassembly differs from the original image; no changes were made.')
            fn.name=p['name'];fn.segment=p['row']['unit'];p['split']=fn
        return names


def run(net,slug,changes,log=print):
    proj=net.project_row(slug)
    if not net.is_rom(proj) or proj['arch']!='mips' or net.fork_slug(slug):raise NetworkError('Reviewed raw-tail recovery currently requires a local MIPS ROM project.')
    if slug in net.links or slug in net.rooms:raise NetworkError('Leave the shared room before changing code boundaries.')
    image,_=net._rom(proj);cfg=net.cfg(proj)
    plans=_plans(net,proj,changes,image)
    configs=sorted(Path(proj['root']).glob('*.yaml'))
    if len(configs)!=1:raise NetworkError('This project needs one original split configuration for reviewed recovery.')
    split_config=yaml.safe_load(configs[0].read_text())
    image_sha1=hashlib.sha1(image).hexdigest()
    if image_sha1!=split_config.get('sha1',cfg['rom']['sha1']):
        raise NetworkError('The analysis image does not match its recorded hash.')
    if program:=cfg['rom'].get('program'):
        original=Path(proj['root'])/program
        if not original.is_file() or hashlib.sha1(original.read_bytes()).hexdigest()!=cfg['rom']['sha1']:
            raise NetworkError('The original cartridge does not match its recorded hash.')
    known={r['vram']:dict(r) for r in net.store.all(f'SELECT vram,size FROM functions WHERE project_id=? AND {IN_BUILD}',proj['id']) if r['vram'] is not None}
    known.update({p['vram']:p for p in plans})
    config=extend_config(split_config,list(known.values()))
    log('Staging reviewed original ranges; no live targets have changed.')
    additions=_stage(net,proj,plans,image,config,log)
    syms=SymbolTable([Symbol(s.name,s.address,s.size,s.kind) for s in net.symbols(proj['id']).by_name.values()])
    for name,address in additions.items():
        if name not in syms.by_name:syms.add(Symbol(name,address,0,'unknown'))
    targets=[net._make_target(proj,p['split'],syms) for p in plans]
    # Recheck input identities and claims after the external analysis; publishing
    # participates in the same saved-progress locking as other layout corrections.
    with net.progress.rebuilding(slug),net.store.tx() as db:
        fresh=net.project_row(slug)
        if fresh['config']!=proj['config']:raise NetworkError('Project settings changed during analysis; run recovery again.')
        for p in plans:
            current=dict(net._function(p['name'],slug))
            if current!=p['row'] or net.store.one('SELECT 1 FROM claims WHERE function_id=?',current['id']):
                raise NetworkError('A function or its claim changed during analysis; run recovery again.')
        _plans(net,fresh,changes,image)
        for p,target in zip(plans,targets):
            meta=json.loads(p['row']['meta'] or '{}')
            meta.setdefault('reviewed_extents',[]).append({'old_size':p['row']['size'],'size':p['size'],'reason':p['reason'],
                'prefix_sha256':hashlib.sha256(p['row']['target']).hexdigest(),'image_sha1':image_sha1})
            for key in ('asm_code','not_c','boundary_review','verified_assembly','asm_twin','set_aside'):meta.pop(key,None)
            meta['reopened']=True
            db.execute("UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,status='open',meta=?,best_source=NULL,best_score=NULL,seed=NULL,seed_key=NULL WHERE id=?",
                       (p['size'],len(p['split'].lines),target.code,json.dumps(target.to_json()),split.function_asm(p['split']),json.dumps(meta),p['row']['id']))
            db.execute('UPDATE symbols SET size=? WHERE project_id=? AND name=?',(p['size'],proj['id'],p['name']))
        for name,address in additions.items():
            db.execute('INSERT OR IGNORE INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,0,?)',(proj['id'],name,address,'unknown'))
        cfg['segments']=[vars(s) for s in split.segments_from_config_data(config)]
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
        net._event(db,proj['id'],None,'boundary','Recovered reviewed raw tails: '+', '.join(p['name'] for p in plans))
        # Drop caches before progress rebuilding renders the changed layout.
        for cache in (net._roms,net._syms,net._xrefs,net._ops,net._warmed):cache.pop(proj['id'],None)
        net.bump_generation(proj['id'])
    net.twins.forget(proj)
    log('Reviewed extents saved as open candidates; no C matches were inferred.')
    return {'recovered':[{'function':p['name'],'old_size':p['row']['size'],'size':p['size']} for p in plans]}
