"""Verified neighbouring C as compiler context, never invented alignment padding.

An IDO entry can contain internal alignment directives. Compiling its real,
already-matched predecessors reproduces the placement; only the freshly judged
entry is exported, using the existing matching-only extraction path.
"""
from io import BytesIO

from elftools.elf.elffile import ELFFile

from ..elfobj import _function_symbols
from ..rom import match

MAX_FUNCTIONS=4


def entries(obj):
    return _function_symbols(ELFFile(BytesIO(obj)))


def verified(net, proj, fn, obj):
    """All auxiliary entries must be exact, matched, contiguous real predecessors."""
    if dict(proj).get('arch')!='mips':return None
    found=entries(obj)
    if not 2<=len(found)<=MAX_FUNCTIONS or fn['name'] not in found or not net.is_rom(proj):return None
    ordered=sorted(found,key=lambda name:found[name][1])
    if ordered[-1]!=fn['name']:return None
    section,start,size=found[fn['name']]
    if size!=fn['size'] or start>16384:return None
    base=fn['vram']-start;rows=[];end=0
    for name in ordered:
        sx,offset,length=found[name]
        if sx!=section or offset!=end:return None
        row=fn if name==fn['name'] else net.store.one(
            'SELECT * FROM functions WHERE project_id=? AND name=?',proj['id'],name)
        if row is None or row['vram']!=base+offset or row['size']!=length:return None
        if name!=fn['name'] and row['status']!='matched':return None
        rows.append(row);end=offset+length
    for row in rows:
        result=match.link(obj,row['name'],net._rom_target(row),net.symbols(proj['id']),net.reader(proj,row))
        if result.exact is not True or result.candidate is None or result.linked_code is None:return None
    return {'ready':False,'state':'requires_normalization','context_functions':ordered[:-1],
            'message':'Exact entry with independently verified predecessor context; matching-only extraction is required to avoid exporting the predecessors twice.'}


def acceptable(net, proj, fn, obj):
    from ..rom.export import _single_definition
    return _single_definition(obj,fn['name']) or bool(verified(net,proj,fn,obj))
