"""Read-only gap audit for supported ROM projects and exported build manifests."""
import argparse
import hashlib
import json
from pathlib import Path

from .. import consoles
from ..arch.mips_gaps import audit
from ..network import IN_BUILD, NetworkError
from ..rom.split import parse_function


def run(net,slug,log=print,*,full_scan=False):
    proj=net.project_row(slug)
    if not net.is_rom(proj) or proj['arch']!='mips':
        raise NetworkError('Gap auditing currently needs a MIPS ROM project; other processors and object builds need a segment adapter.')
    from .code_audit_reviews import revision
    input_revision=revision(net,proj['id'])
    image,segments=net._rom(proj)
    from .analysis_image import verify
    verify(image,proj,net.cfg(proj),check=getattr(log,'check_cancel',lambda:None))
    console=net.console(proj)
    functions=[dict(name=f['name'],rom=f['rom_offset'],vram=f['vram'],size=f['size'],
                    handwritten=bool(console.asm_reason(f['target']))) for f in net.project_store(proj['id']).all(
                        f'SELECT name,rom_offset,vram,size,target FROM functions WHERE project_id=? AND {IN_BUILD}',proj['id']) if f['rom_offset'] is not None]
    log('Reading frozen ROM bytes and known code segment mappings; no build or compiler is run.')
    entry=net.cfg(proj).get('rom',{}).get('entrypoint')
    # An imported analysis image may map the entry differently from the original header. Only use
    # an explicit, unscoped entry segment; a replaced boot or overlay is not the original entry.
    mapped_entries={s.vram for s in segments if s.name=='entry' and not s.exclusive_ram_id}
    entries=tuple(dict.fromkeys(([entry] if type(entry) is int else []) +
                                (list(mapped_entries) if len(mapped_entries)==1 else [])))
    callback_checkpoint=None
    if full_scan:
        from .code_audit_reviews import indirect_checkpoint
        callback_checkpoint=indirect_checkpoint(net,proj['id'],input_revision,image,functions,
                                                'big' if proj['big_endian'] else 'little')
    result=audit(image,[vars(s) for s in segments],functions,big_endian=bool(proj['big_endian']),cpu=console.cpu_variant,
                 progress=getattr(log,'progress',lambda _:None),data_mappings=net.cfg(proj).get('audit_data_mappings',[]),
                 full_scan=full_scan,check=getattr(log,'check_cancel',lambda:None),
                 entrypoints=entries,callback_checkpoint=callback_checkpoint)
    if full_scan:
        from .code_audit_reviews import runtime_checkpoint
        rom_cfg=net.cfg(proj).get('rom',{})
        source=Path(proj['root'])/(rom_cfg.get('program') or rom_cfg.get('file',''))
        source_identity='unavailable'
        if source.is_file():
            with source.open('rb') as stream:source_identity=hashlib.file_digest(stream,'sha256').hexdigest()
        checkpoint=runtime_checkpoint(net,proj['id'],input_revision+':'+source_identity,image)
        evidence=console.runtime_audit_candidates(Path(proj['root']),net.cfg(proj),image,segments,functions,log,
                                                  result['candidates'],checkpoint)
        indexed={(r['rom_start'],r['vram'],r['rom_end']-r['rom_start']):r for r in evidence
                 if r.get('kind')!='runtime_location'}
        locations={}
        for item in evidence:
            if item.get('kind')=='runtime_location':
                locations.setdefault((item['rom_start'],item['rom_end']-item['rom_start']),[]).append(
                    {'vram':item['vram'],'snapshots':item['snapshots'],
                     'executed':bool(item.get('executed')),
                     **({'direct_call_site':item['direct_call_site']} if 'direct_call_site' in item else {}),
                     **({'execution_retrace':item['execution_retrace']} if item.get('executed') else {})})
        for row in result['candidates']:
            key=(row['rom_offset'],row['vram'],row['extent_bytes'])
            if key in indexed:
                row['runtime_scope']={'scope':indexed[key]['scope'],
                                      'co_resident_snapshots':indexed[key]['co_resident_snapshots']}
            if found:=locations.get((row['rom_offset'],row['extent_bytes'])):
                row['runtime_locations']=found
        result['candidates'].sort(key=lambda r:(not any('direct_call_site' in x for x in r.get('runtime_locations',[])),
            not any(x.get('executed') for x in r.get('runtime_locations',[])),
            not bool(r.get('runtime_scope')),not bool(r.get('runtime_locations')),not bool(r['incoming']),
            not r['direct_flow_closed'],not any(i.get('mapping_relation')=='same' for i in r['incoming']),
            r['rom_offset'],r['vram']))
    log(f"Reviewed {result['gaps']} gaps; {len(result['candidates'])} possible code regions need human review.")
    from .code_audit_reviews import record
    result['input_revision']=input_revision
    return record(net,proj['id'],result,image,check=getattr(log,'check_cancel',lambda:None))


def exported(root):
    manifest=json.loads((root/'build.json').read_text());console=consoles.get(manifest['platform'])
    if console.arch!='mips':raise ValueError('This audit currently supports MIPS ROM exports.')
    path=(root/manifest['image']).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('The ROM path must stay inside the export.')
    image=path.read_bytes()
    if hashlib.sha1(image).hexdigest()!=manifest['sha1']:raise ValueError('The ROM hash differs from the export manifest.')
    functions=[]
    for fn in manifest['functions']:
        assembly=(root/fn['asm']).resolve()
        if not assembly.is_relative_to(root.resolve()):raise ValueError('Assembly paths must stay inside the export.')
        parsed=parse_function(assembly,fn['seg'],console.big_endian)
        if parsed:
            size=parsed.size;offset=fn['rom']
            if offset is None or offset<0 or offset+size>len(image):raise ValueError('A function exceeds the verified image.')
            functions.append(dict(name=fn['name'],rom=offset,vram=fn['vram'],size=size,
                                  handwritten=bool(console.asm_reason(image[offset:offset+size]))))
    segments=[dict(rom_start=s['start'],rom_end=s['end'],vram=s['vram']) for s in manifest['segments'] if s['kind']=='code']
    # The build manifest does not preserve scope identities. The exported Splat config does;
    # use its entry only when it agrees with the build mapping and is not a replaced boot.
    entrypoints=()
    config=(root/'splat.yaml').resolve()
    if config.is_relative_to(root.resolve()) and config.is_file():
        import yaml
        try:
            raw=yaml.safe_load(config.read_text(encoding='utf-8'))
            entries=[s for s in (raw.get('segments') or []) if isinstance(s,dict) and s.get('name')=='entry'
                     and s.get('type')=='code' and not s.get('exclusive_ram_id')]
            if len(entries)==1 and type(entries[0].get('vram')) is int and any(
                                       s['rom_start']==entries[0].get('start') and s['vram']==entries[0].get('vram')
                                       for s in segments):
                entrypoints=(entries[0]['vram'],)
        except (OSError,ValueError,TypeError,AttributeError,yaml.YAMLError):
            pass  # Older or edited exports still get the audit without an assumed entry.
    return audit(image,segments,functions,big_endian=console.big_endian,cpu=console.cpu_variant,
                 data_mappings=manifest.get('data_mappings',[]),entrypoints=entrypoints)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:result=exported(args.project)
    except (OSError,ValueError,KeyError) as e:parser.error(str(e))
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='candidates'}))


if __name__=='__main__':main()
