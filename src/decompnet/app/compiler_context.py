"""Independently verified whole-unit compiler context; only the chosen entry is published.

Private IDO procedures need the real caller/callee unit to reproduce its register
convention. Hidden procedures come from explicit compiler debug extents, never
from target instruction matching or guessed source boundaries. Public procedures
can also need an already-matched address-taking caller as compiler context; those
callers are independently rechecked at their original addresses before extraction.
"""
from io import BytesIO

from elftools.elf.elffile import ELFFile

from ..elfobj import _function_symbols, metadata_functions, metadata_placeholders
from ..rom import match
from . import predecessor_context

MAX_FUNCTIONS=4
MAX_CODE=16384


def verified(net, proj, fn, obj):
    proof=predecessor_context.verified(net,proj,fn,obj)
    return proof or private_unit(net,proj,fn,obj)


def private_unit(net, proj, fn, obj):
    """Every actual unit body must match its known ROM entry; auxiliary statuses are not changed."""
    if dict(proj).get('arch')!='mips' or not net.is_rom(proj):return None
    elf=ELFFile(BytesIO(obj));found=_function_symbols(elf)
    if elf.elfclass!=32 or elf['e_type']!='ET_REL' or not 1<=len(found)<=MAX_FUNCTIONS or fn['name'] not in found:return None
    symtab=elf.get_section_by_name('.symtab')
    exported={s.name for s in symtab.iter_symbols() if s['st_info']['bind']=='STB_GLOBAL' and isinstance(s['st_shndx'],int)} if symtab else set()
    hidden=set(metadata_functions(elf))-exported
    public=not hidden and len(found)>1 and set(found)<=exported
    if not hidden and not public:return None
    section,start,size=found[fn['name']]
    if size!=fn['size']:return None
    rows=[];end=0
    ordered=sorted(found,key=lambda name:found[name][1])
    placeholders=sorted(metadata_placeholders(elf).values(),key=lambda span:span[1])
    for name in ordered:
        sx,offset,length=found[name]
        for px,at,amount in placeholders:
            if px==section and at==end and end<offset:end+=amount
        if sx!=section or offset!=end or offset+length>MAX_CODE:return None
        row=fn if name==fn['name'] else net.store.one(
            'SELECT * FROM functions WHERE project_id=? AND name=?',proj['id'],name)
        if row is None or row['size']!=length:return None
        if public and name!=fn['name'] and row['status']!='matched':return None
        rows.append(row);end=offset+length
    # A bounded subset can reproduce calling or address-taking context without the
    # unrelated functions between its retail slots. Every call is linked through
    # the known ROM names, and every body is independently judged at its own slot;
    # never infer a target address from the compiler's unit-relative offsets.
    target_spans=sorted((row['vram'],row['vram']+row['size']) for row in rows)
    if any(a[1]>b[0] for a,b in zip(target_spans,target_spans[1:])):return None
    for row in rows:
        result=match.link(obj,row['name'],net._rom_target(row),net.symbols(proj['id']),net.reader(proj,row))
        if result.exact is not True or result.candidate is None or result.linked_code is None:return None
    return {'ready':False,'state':'requires_normalization','context_functions':[n for n in ordered if n!=fn['name']],
            'message':'Exact entry and independently verified compiler unit; matching-only extraction is required. Auxiliary entries are context, not newly published matches.'}


def acceptable(net, proj, fn, obj):
    from ..rom.export import _single_definition
    return _single_definition(obj,fn['name']) or bool(verified(net,proj,fn,obj))


def single_source(source, header, name, obj):
    """Turn other emitted C definitions into declarations, retaining line positions. A room candidate may carry
    neighbouring bodies, but publishing one function must compile and rejudge that function independently."""
    from ..forks import csource

    found = _function_symbols(ELFFile(BytesIO(obj)))
    if name not in found or not 1 < len(found) <= MAX_FUNCTIONS or csource.find(source, name) is None:
        return None
    texts = [source, header]
    for other in sorted(set(found) - {name}):
        for index, text in enumerate(texts):
            span = csource.find(text, other)
            if span is None:
                continue
            start, end = span
            brace = csource._code_only(text).find('{', start, end)
            if brace < 0:
                return None
            texts[index] = text[:brace] + ';' + ''.join('\n' if c == '\n' else ' ' for c in text[brace+1:end]) + text[end:]
            break
        else:
            return None  # mangled names, macros or included definitions need their original compiler context
    return tuple(texts)
