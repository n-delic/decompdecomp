"""Evidence-led reference context diagnostics. Never infer a layout, ABI or macro body."""
import re

from ..forks import csource
from ..network import NetworkError

IDENT=r'[A-Za-z_]\w*'
CONTROL={'case','if','for','while','switch','sizeof','return','defined','__attribute__'}


def prototypes(original, source, header, entry):
    """Carry actual same-file private helper signatures, not implicit-int guesses."""
    result=[]
    for name in sorted(set(re.findall(r'\b('+IDENT+r')\s*\(',csource._blank(source)))-CONTROL-{entry}):
        if re.search(r'\b'+re.escape(name)+r'\s*\(',csource._blank(header)):continue
        span=csource.find(original,name)
        if not span or csource.is_stub(original[span[0]:span[1]]):continue
        declaration=original[span[0]:span[1]].split('{',1)[0].strip()
        # K&R signatures require their parameter declarations; don't synthesize an ABI.
        if ';' in declaration or not declaration.endswith(')'):continue
        declaration=re.sub(r'\bstatic\b','extern',declaration)
        result.append(declaration+';')
    return '\n'+'\n'.join(result)+'\n' if result else ''


def helpers(reader, cfg, source, header, macros):
    """Keep selected static definitions verbatim; judge rejects non-inlined extra code."""
    from . import source_conditions, source_definitions
    header_original=header
    selected=[];used=set(re.findall(IDENT,csource._blank(source)))
    pending=source_definitions.mappings(cfg.get('helpers',''))
    for _ in range(8):
        before=len(selected)
        for name,file in pending:
            if name not in used or any(n==name for n,_ in selected):continue
            text,_=source_conditions.select(reader.flatten(reader.root/file),macros,reader.overrides)
            span=csource.find(text,name)
            if not span:raise NetworkError(f'No static helper definition for {name!r}; select its actual definition file.')
            code=text[span[0]:span[1]]
            if not re.search(r'\bstatic\b',csource._blank(code).split('{',1)[0]):
                raise NetworkError('Only explicitly selected static helper definitions can enter the private header.')
            from .source_type_context import referenced
            header += referenced(text,code,header)
            selected.append((name,f'#line {text[:span[0]].count(chr(10))+1} "reference-helper.c"\n'+code))
            used.update(re.findall(IDENT,csource._blank(code)))
        if before==len(selected):break
    # Forward declarations keep dependency ordering independent of the setting's order.
    forward=[]
    for _,code in selected:
        declaration=code.split('{',1)[0]
        if ';' in declaration:raise NetworkError('Supply an ANSI declaration subset for K&R static helpers.')
        forward.append(declaration+';\n')
    return '\n'+header[len(header_original):]+''.join(forward)+''.join(code for _,code in selected)


def context_hints(source, header):
    clean=csource._blank(header);result=[]
    # Duplicate typedef aliases with differing declarations are evidence of mixed
    # context, not permission to choose fields or silently drop a definition.
    aliases={}
    for m in re.finditer(r'\btypedef\b[^;]*?\b('+IDENT+r')\s*;',clean,re.S):
        normalized=' '.join(m[0].split());old=aliases.get(m[1])
        if old and old!=normalized:
            result.append(f'Conflicting typedef {m[1]}: check that the source and header use the same SDK revision. No layout was changed.')
        aliases[m[1]]=normalized
    for name in sorted(set(re.findall(r'\b([A-Z_][A-Z_0-9]{2,})\s*\(',csource._blank(source)))):
        if not re.search(r'\b'+re.escape(name)+r'\b',clean):
            result.append(f'{name} is called but absent from the prepared header. Check the original macro/prototype before permutation; no replacement was invented.')
    return result[:8]


def compiler_hints(output):
    lines=[line.strip() for line in str(output).splitlines()
           if re.search(r'implicit.*(?:declar|function)|(?:function|routine).*implicitly|undeclared',line,re.I)]
    if not lines:return []
    return ['Check missing original macros and private helper prototypes before permutation: '+line[:400] for line in lines[:4]]


def typedefs(text):
    """Conservative single-alias typedef declarations, including nested records."""
    clean=csource._blank(text);result={}
    for m in re.finditer(r'\btypedef\b',clean):
        depth=0
        for i in range(m.end(),len(clean)):
            char=clean[i]
            depth+=(char in '{([')-(char in '})]')
            if char==';' and depth==0:
                declaration=clean[m.start():i+1]
                alias=re.search(r'\b('+IDENT+r')\s*;$',declaration)
                if alias:result[alias[1]]=' '.join(declaration.split())
                break
    return result


def compatibility(original, source, header):
    """Report actual differing declarations, without asserting SDK identity or changing fields."""
    reference=typedefs(original);destination=typedefs(header)
    used=set(re.findall(IDENT,csource._blank(source)))
    return [f'Reference and prepared header declare {name} differently. Check SDK revision compatibility or select a coherent context alternative; no fields were changed.'
            for name in sorted(used & reference.keys() & destination.keys())
            if reference[name]!=destination[name]][:8]
