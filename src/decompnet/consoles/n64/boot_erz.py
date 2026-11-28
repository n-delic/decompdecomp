"""Recover a boot program loaded from a table of ERZ-compressed pages."""
import struct

from decompassets.rnc import RncError, unpack, unpack_m2

from ...arch.mips import JR_RA
from ...arch.mips_assets import read
from .boot_two_bit import code_start

_PAGE=0x10000
_LIMIT=4*1024*1024


def _program(rom,table):
    if not 0<=table<=len(rom)-12:return None
    count=struct.unpack_from('>I',rom,table)[0]
    if not 2<=count<=_LIMIT//_PAGE or table+4*(count+2)>len(rom):return None
    offsets=struct.unpack_from(f'>{count+1}I',rom,table+4)
    if offsets[0]<4*(count+2) or any(a>=b for a,b in zip(offsets,offsets[1:])) or table+offsets[-1]>len(rom):return None
    output=bytearray()
    for i,(a,b) in enumerate(zip(offsets,offsets[1:])):
        block=rom[table+a:table+b]
        if block[:3]==b'ERZ':
            if len(block)<18 or block[3] not in (1,2):return None
            packed=int.from_bytes(block[8:12],'big')
            if 18+packed>len(block):return None
            try:
                data=(unpack if block[3]==1 else unpack_m2)(b'RNC'+block[3:18+packed],max_output=_PAGE)
            except RncError:
                return None
        else:
            data=block
        if not 0<len(data)<=_PAGE or (i<count-1 and len(data)!=_PAGE):return None
        output.extend(data)
    return bytes(output)


def _destinations(code):
    """Find an initialized page destination passed to a loader and advanced by 64 KiB."""
    words=[w for w, in struct.iter_unpack('>I',code[:len(code)&~3])]
    found=set()
    for i,first in enumerate(words):
        if first>>26!=15:continue
        source=(first>>16)&31
        for second in words[i+1:i+9]:
            if second>>26==9 and (second>>21)&31==source:
                reg=(second>>16)&31;low=second&0xffff
                if not reg:continue
                value=(((first&0xffff)<<16)+(low-0x10000 if low&0x8000 else low))&0xffffffff
                if not 0x80000400<=value<0x80800000:continue
                passed=any(((w>>26==0 and (w>>21)&31==reg and (w>>16)&31==0 and (w>>11)&31==5 and w&63 in (0x21,0x25))
                            or (w>>26==9 and (w>>21)&31==reg and (w>>16)&31==5 and not w&0xffff))
                           and any(x>>26==3 for x in words[max(i+1,j-1):j+3])
                           for j,w in enumerate(words[i+1:i+513],i+1))
                advanced=any(w>>26==0 and w&63==0x21 and (w>>21)&31==reg and (w>>11)&31==reg
                             and any(x>>26==15 and (x>>16)&31==(w>>16)&31 and x&0xffff==1
                                     for x in words[max(0,j-16):j]) for j,w in enumerate(words[i+1:i+513],i+1))
                if passed and advanced:found.add(value)
    return found


def find(rom,boot_ranges):
    """Require a boot-referenced table, verified page CRCs, destination and a call into code."""
    from .code import _looks_like_code

    matches={}
    for lo,hi,base in boot_ranges:
        if lo>=0x20000 or hi>len(rom):continue
        code=rom[lo:hi];words=[w for w, in struct.iter_unpack('>I',code[:len(code)&~3])]
        if not any(a>>26==15 and a&0xffff==0x4552 and b>>26==13 and b&0xffff==0x5a00
                   and (a>>16)&31==(b>>21)&31==(b>>16)&31 for a,b in zip(words,words[1:])):continue
        events=read(base,code)['events'];calls=[e for e in events if e['kind']=='call']
        tables={arg['value']&0x0fffffff for e in calls for arg in e.get('args',())
                if arg and arg.get('kind')=='constant' and 0xb0000000<=arg['value']<0xc0000000}
        targets={e['target'] for e in calls if e.get('target') is not None}
        for table in tables:
            data=_program(rom,table)
            if data is None or not _looks_like_code(data):continue
            start=code_start(data)
            if start is None:continue
            ends=[i+8 for i in range(start,len(data)-7,4) if int.from_bytes(data[i:i+4],'big')==JR_RA]
            if not ends:continue
            end=ends[-1]
            for dest in _destinations(code):
                if dest%16 or dest+len(data)>0x80800000:continue
                if not any(dest+start<=target<dest+end for target in targets):continue
                if any(dest<b+e-s and b<dest+len(data) for s,e,b in boot_ranges):continue
                matches[table,dest]=((dest,data[:end],data[end:]),)
    return next(iter(matches.values())) if len(matches)==1 else None
