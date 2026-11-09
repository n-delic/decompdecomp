"""Read-only gap audit for supported ROM projects and exported build manifests."""
import argparse
import hashlib
import json
from pathlib import Path

from .. import consoles
from ..arch.mips_gaps import audit
from ..network import IN_BUILD, NetworkError
from ..rom.split import parse_function


def run(net,slug,log=print):
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
    result=audit(image,[vars(s) for s in segments],functions,big_endian=bool(proj['big_endian']),cpu=console.cpu_variant,progress=getattr(log,'progress',lambda _:None),data_mappings=net.cfg(proj).get('audit_data_mappings',[]))
    log(f"Reviewed {result['gaps']} gaps; {len(result['candidates'])} bounded candidates need human review.")
    from .code_audit_reviews import record
    result['input_revision']=input_revision
    return record(net,proj['id'],result,image)


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
    return audit(image,segments,functions,big_endian=console.big_endian,cpu=console.cpu_variant,data_mappings=manifest.get('data_mappings',[]))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:result=exported(args.project)
    except (OSError,ValueError,KeyError) as e:parser.error(str(e))
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='candidates'}))


if __name__=='__main__':main()
