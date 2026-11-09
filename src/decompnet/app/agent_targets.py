"""Conservative source anchors and compact family suggestions; no compilation or source writes."""

import re

from ..network import NetworkError

TYPE = r'(?:(?:unsigned|signed)\s+)?(?:int|short|long|char|float|double|[su](?:8|16|32|64))'
DECL = re.compile(r'^([ \t]*'+TYPE+r'\s+\*?\s*([A-Za-z_]\w*)\s*;)[ \t]*$', re.M)


def declarations(source):
    # Hide strings and comments while preserving positions so examples inside them aren't choices.
    masked = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), source, flags=re.S)
    return [(m[2], m.start(1), m.end(1)) for m in DECL.finditer(masked)
            if DECL.fullmatch(source[m.start(1):m.end(1)])]


def valid_targets(source, family):
    if family not in ('signedness','declaration_order'):
        raise NetworkError('Named targets support signedness or declaration_order; casts needs an expression anchor.')
    items = declarations(source)
    names = [d[0] for d in items]
    targets = []
    for i,(name,start,end) in enumerate(items):
        if names.count(name) != 1:
            continue
        label = name
        if family == 'signedness':
            if re.match(r'\s*(?:float|double)\b',source[start:end]):
                continue
        else:
            if i+1 == len(items):
                continue
            other,next_start,next_end = items[i+1]
            gap = source[end:next_start]
            if gap.strip() or gap.count('\n') != 1 or names.count(other) != 1:
                continue
            end = next_end
            label += ' -> '+other
        targets.append({'name':name,'label':label,'line':source.count('\n',0,start)+1,'old':source[start:end]})
    return targets


def choices(source, family=None):
    if family in ('signedness','declaration_order'):
        items = valid_targets(source,family)
        return ('Valid '+family+' targets: '+', '.join(f"{t['label']} (line {t['line']})" for t in items[:6])
                if items else 'No valid '+family+' targets in this candidate. Use an explicit patch or a different family.')
    items = declarations(source)
    return ('Simple declaration choices: '+', '.join(f'{n} (line {source.count(chr(10),0,a)+1})' for n,a,b in items[:6])
            if items else 'No simple uninitialized declaration choices found; supply an exact expression/declaration anchor.')


def named_anchor(source, name, family):
    targets = [t for t in valid_targets(source,family) if t['name']==name]
    if len(targets) != 1:
        raise NetworkError('Declaration name must identify exactly one valid target. '+choices(source,family))
    return targets[0]['old']


def replace_anchor(source, old, new, occurrence=0):
    if not isinstance(old,str) or not old or not isinstance(new,str):
        raise NetworkError('Supply nonempty old text occurring exactly once and replacement new text.')
    positions = [m.start() for m in re.finditer(re.escape(old), source)]
    if isinstance(occurrence,bool) or not isinstance(occurrence,int) or occurrence < 0:
        raise NetworkError('occurrence must be a positive 1-based integer, or 0 for a unique anchor.')
    if not positions or (occurrence == 0 and len(positions) != 1) or occurrence > len(positions):
        lines = ', '.join(str(source.count('\n',0,p)+1) for p in positions[:6])
        nearby=[]
        if positions:
            for i,pos in enumerate(positions[:6],1):
                begin=source.rfind('\n',0,pos)+1;end=source.find('\n',pos)
                nearby.append(f"occurrence {i}, line {source.count(chr(10),0,pos)+1}: "+source[begin:end if end>=0 else len(source)][:160])
        else:
            import difflib
            for line in difflib.get_close_matches(old.strip(),source.splitlines(),n=3,cutoff=.45):
                nearby.append('Possible current line (not applied): '+line[:160])
        raise NetworkError(f'Old text must occur exactly once unless occurrence is supplied. Found {len(positions)} occurrences'
                           + (f' on lines {lines}; select occurrence=1..{len(positions)}.' if positions else '. '+choices(source))
                           + ('\n'+'\n'.join(nearby) if nearby else ''))
    start = positions[(occurrence or 1)-1]
    return source[:start]+new+source[start+len(old):]


def suggestions(source):
    result = []
    for family in ('signedness','declaration_order'):
        items = valid_targets(source,family)
        if items:
            result.append(f"{family}(declaration={items[0]['name']})")
    return 'Local families available: '+', '.join(result)+'. Use compile_family with the candidate revision.\n' if result else ''
