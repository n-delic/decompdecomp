"""Materialize one freshly compiled, linked, exact MIPS entry for matching-only exports.

This is a build step, never a target-byte fallback. It runs the ordinary relocation
and constant-data judge on every compile, then emits the compiler's relocated code.
Other bodies and pool layout stay out of the entry's slot; original data is retained.
"""
import hashlib
import struct
from io import BytesIO

from elftools.elf.elffile import ELFFile

from decompnet.rom import match, split
from decompnet.rom.symbols import Symbol, SymbolTable


def object_for(obj, name, code):
    """A minimal ELF32 relocatable entry, with the compiler object's ABI flags."""
    elf=ELFFile(BytesIO(obj))
    if elf.elfclass!=32 or elf['e_machine']!='EM_MIPS' or elf['e_type']!='ET_REL':
        raise ValueError('Entry extraction requires an ELF32 MIPS relocatable compiler object.')
    if not code or len(code)%4:raise ValueError('The compiled entry must contain complete instructions.')
    order='<' if elf.little_endian else '>';names=b'\0.text\0.symtab\0.strtab\0.shstrtab\0';strings=b'\0'+name.encode('ascii')+b'\0'
    data=bytearray(52);sections=[(0,0,0,0,0,0,0,0,0,0)]
    def section(label,kind,flags,payload,link=0,info=0,align=4,entsize=0):
        data.extend(bytes(-len(data)%align));at=len(data);data.extend(payload)
        sections.append((names.index(label.encode()+b'\0'),kind,flags,0,at,len(payload),link,info,align,entsize))
    section('.text',1,6,code)
    symbols=bytes(16)+struct.pack(order+'IIIBBH',1,0,len(code),0x12,0,1)
    section('.symtab',2,0,symbols,3,1,4,16);section('.strtab',3,0,strings,align=1);section('.shstrtab',3,0,names,align=1)
    data.extend(bytes(-len(data)%4));shoff=len(data)
    for s in sections:data.extend(struct.pack(order+'10I',*s))
    ident=bytearray(obj[:16]);ident[4]=1
    header=bytes(ident)+struct.pack(order+'HHIIIIIHHHHHH',1,8,1,0,0,shoff,elf['e_flags'],52,0,0,40,len(sections),4)
    data[:52]=header
    return bytes(data)


def write(root, manifest, fn, path):
    """Rejudge the current compiled object against the verified image before extracting its entry."""
    image=(root/manifest['image']).read_bytes()
    if hashlib.sha1(image).hexdigest()!=manifest['sha1']:raise ValueError('Restore the verified game image before extracting an entry.')
    table=SymbolTable([Symbol(n,a,0,'data') for n,a in manifest['symbols'].items()]+
                      [Symbol(f['name'],f['vram'],0,'func') for f in manifest['functions']])
    target=match.target_from_split(split.parse_function(root/fn['asm'],fn['seg'],manifest['big_endian']),table.resolve,
                                   manifest['big_endian'],manifest.get('gp') or 0)
    # Some old exports kept readonly pools as raw blobs, outside their code
    # parents. Only explicit reviewed mappings may expose those bytes; never
    # guess an address delta merely to make the judge accept a constant.
    ranges=[dict(rom_start=s['start'],rom_end=s['end'] or len(image),vram=s['vram'])
            for s in manifest['segments'] if s['kind']=='code']+manifest.get('data_mappings',[])
    for s in ranges:
        if not 0<=s['rom_start']<s['rom_end']<=len(image):raise ValueError('A reviewed data mapping exceeds the verified image.')
    missed=[]
    def read(addr,n):
        hits=[s for s in ranges if s['vram']<=addr and addr+n<=s['vram']+s['rom_end']-s['rom_start']]
        offsets={s['rom_start']+addr-s['vram'] for s in hits}
        if len(offsets)>1:raise ValueError(f'Overlapping image mappings disagree about constant 0x{addr:08X} ({n} bytes).')
        if offsets:
            at=offsets.pop();return image[at:at+n]
        missed.append((addr,n))
        return None
    obj=path.read_bytes();linked=match.link(obj,fn['name'],target,table,read)
    if not linked.exact or linked.linked_code is None:
        raise ValueError('Entry extraction requires freshly verified exact code and data: '+'; '.join(linked.problems))
    if missed:
        details=', '.join(f'0x{addr:08X}–0x{addr+n:08X} ({n} bytes)' for addr,n in sorted(set(missed))[:16])
        raise ValueError(f'{fn["name"]}: unverified constant reads: {details}. End addresses are exclusive. Supply reviewed data_mappings in build.json; do not guess a ROM address delta.')
    path.write_bytes(object_for(obj,fn['name'],linked.linked_code))
