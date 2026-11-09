"""Permit bounded repairs outside opaque macro regions; never expand or rewrite directives."""
import difflib
import re

from ..forks.csource import _blank

IDENT = r'[A-Za-z_]\w*'


def prepare(source, header=''):
    clean = _blank(source)
    lines = list(re.finditer(r'(?m)^[ \t]*#[^\n]*(?:\n|$)',clean))
    definitions = {}
    protected = []
    for text in (header,source):
        for line in re.finditer(r'(?m)^[ \t]*#[^\n]*(?:\n|$)',_blank(text)):
            match = re.fullmatch(r'[ \t]*#[ \t]*define[ \t]+('+IDENT+r')(?:\([^\n)]*\))?[^\n]*\n?',line[0])
            if not match or '\\' in line[0] or '#' in line[0].lstrip()[1:] or re.search(r'\b(?:volatile|restrict|asm|__asm__)\b',line[0]):
                return None,[], 'Macro repair coverage: conditional, multiline, included or token-pasting directives require preprocessing evidence; deterministic macro-region repairs skipped.'
            definitions[match[1]] = set(re.findall(IDENT,line[0])) - {match[1],'define'}
    # Transitive hidden dependencies are opaque too (including object-like aliases).
    names = set(definitions)
    changed = True
    while changed:
        expanded = names | set().union(*(definitions[n] for n in names if n in definitions)) if names else set()
        changed = expanded != names
        names = expanded
    protected.extend((m.start(),m.end()) for m in lines)
    for hit in re.finditer(IDENT,clean):
        if hit[0] not in names:
            continue
        start = max(clean.rfind(ch,0,hit.start()) for ch in ';{}\n')+1
        stops = [p for p in (clean.find(ch,hit.end()) for ch in ';{}\n') if p>=0]
        end = min(stops)+1 if stops else len(clean)
        protected.append((start,end))
    masked = source
    for line in reversed(lines):
        masked = masked[:line.start()]+''.join('\n' if ch=='\n' else ' ' for ch in source[line.start():line.end()])+masked[line.end():]
    return masked,protected,'Macro repair coverage: directives and macro-dependent statements are protected; only unaffected regions are eligible.'


def variants(source,name,generate,header=''):
    masked,protected,_ = prepare(source,header)
    if masked is None:
        return []
    out = []
    for family,candidate in generate(masked):
        if '#' in _blank(candidate):continue
        edits=[]
        for tag,a,b,c,d in difflib.SequenceMatcher(a=masked,b=candidate,autojunk=False).get_opcodes():
            if tag=='equal':continue
            if any((a<end and b>start) or (a==b and start<=a<=end) for start,end in protected):
                break
            edits.append((a,b,candidate[c:d]))
        else:
            if edits and any(min(e[0] for e in edits)<end and max(e[1] for e in edits)>start for start,end in protected):
                continue  # a statement must not move across an opaque macro effect
            text=source
            for a,b,value in reversed(edits):text=text[:a]+value+text[b:]
            if edits and (family,text) not in out:out.append((family,text))
        if len(out)>=24:break
    return out


def diagnostic(source):
    return prepare(source)[2] if re.search(r'(?m)^[ \t]*#',_blank(source)) else ''
