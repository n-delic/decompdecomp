"""Original file-scope objects selected explicitly or referenced in the mapped source file."""
import re

from ..forks.csource import _blank
from ..network import NetworkError


def mappings(text):
    result=[];seen=set()
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        m=re.fullmatch(r'\s*([A-Za-z_]\w*)\s*@\s*(.+?)\s*',line)
        if not m or m[1] in seen:raise NetworkError('Use one definition per line: reference_symbol @ relative/file.c')
        seen.add(m[1]);result.append((m[1],m[2]))
        if len(result)>64:raise NetworkError('Select at most 64 object definitions.')
    return result


def extract(text, name):
    # Function bodies have already been removed. Top-level semicolons delimit declarations,
    # including aggregate initializers; strings/comments remain opaque and line positions stable.
    clean=_blank(text);clean=re.sub(r'(?m)^[ \t]*#[^\n]*',lambda m:' '*len(m[0]),clean)
    start=depth=0;found=[]
    for i,char in enumerate(clean):
        depth+=(char=='{')-(char=='}')
        if char!=';' or depth:continue
        line_number=text[:start].count('\n')+1
        part=clean[start:i+1];original=text[start:i+1];start=i+1
        match=re.search(r'\b'+re.escape(name)+r'\s*(?:\[[^\]\n]*\]\s*)*(?==|;)',part)
        if not match:continue
        if re.search(r'\b(?:typedef|extern)\b',part[:match.start()]) or '(' in part[:match.start()] or ',' in part[:match.end()]:
            raise NetworkError('Select a single file-scope object definition; split multiple declarations into a prepared subset.')
        nesting=0
        for token in part:
            if token in '({[':nesting+=1
            elif token in ')}]':nesting-=1
            elif token==',' and nesting==0:
                raise NetworkError('Select one object per declaration; supply a prepared definition subset.')
        # Strip preceding directives without moving the object's lines or keeping unrelated includes.
        original=re.sub(r'(?m)^[ \t]*#[^\n]*',lambda m:re.sub(r'[^\n]',' ',m[0]),original)
        found.append(f'#line {line_number} \"reference-definitions.c\"\n'+original)
    if len(found)!=1:raise NetworkError(f'Expected one object definition for {name!r}; supply a prepared definition subset.')
    return found[0]


def externalize(definition):
    """Give a selected file-scope object external linkage without moving its lines."""
    clean = _blank(definition)
    match = re.search(r'\bstatic[ \t]+', clean)
    if match:
        definition = definition[:match.start()] + ' ' * len(match[0]) + definition[match.end():]
    return definition


def related(text, used):
    """Unambiguous definitions in the explicitly mapped file; no search outside that file."""
    found=[]
    for name in sorted(used)[:64]:
        try:code=extract(text,name)
        except NetworkError:continue
        found.append((name,code))
        if len(found)>=8:break
    return found
