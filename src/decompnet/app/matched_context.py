"""Bounded, attributed declaration references from exact matches; never edit live headers."""
import re

from ..forks.csource import _blank, find, is_stub
from ..network import NetworkError
from .source_match_context import macro_context
from .source_type_context import referenced

ID = r'[A-Za-z_]\w*'


def declarations(text, name, source):
    """Only a real matched definition supplies a signature; retain its type/macro closure."""
    span = find(text,name)
    if not span or is_stub(text[span[0]:span[1]]):
        return ''
    definition = re.search(r'\b'+re.escape(name)+r'\s*\([^{};]*\)\s*{',text[span[0]:span[1]])
    if not definition:
        return ''
    at = span[0]+definition.start()
    start = max(text.rfind(';',span[0],at),text.rfind('}',span[0],at),span[0]-1)+1
    signature = text[start:span[0]+definition.end()-1].strip()
    if ';' in signature or not signature.endswith(')') or re.search(r'\bstatic\b',signature):
        return ''
    prototype = signature+';\n'
    body = text[span[0]:span[1]]
    types = referenced(text,prototype+source+body,'')
    try:
        macros = macro_context(text,source+body,types)
    except NetworkError:
        macros = ''  # Conflicting references remain discoverable in the original function.
    return types+'\n'+prototype+macros


def brief(net, fn, source):
    if not source:
        return []
    calls = sorted(set(re.findall(r'\b('+ID+r')\s*\(',_blank(source)))-{fn['name'],'if','while','for','switch','sizeof'})[:16]
    if not calls:
        return []
    # Indexed project/unit lookup, capped rows and text. No all-project scan on a brief.
    marks=','.join('?' for _ in calls)
    rows=net.store.all("SELECT name,unit,substr(matched_source,1,16000) AS source FROM functions "
                       "WHERE project_id=? AND status='matched' AND name IN ("+marks+") "
                       "ORDER BY CASE WHEN unit=? THEN 0 ELSE 1 END,id LIMIT 4",
                       fn['project_id'],*calls,fn['unit'])
    out=[]
    for row in rows:
        context=declarations(row['source'] or '',row['name'],source)
        if not context or len(context)>5000:
            continue
        out += [f"Declarations from exact-matched {row['name']} ({row['unit']}); source evidence, not proof of a unique type or ABI:",
                '```c\n'+context.strip()+'\n```']
    if out:
        out=['### Matched callee and library context',
             'Review these actual declarations and reachable macros before rebuilding raw-offset layouts or library routines. '
             'Check conflicts with the current header; compile and judge any adopted context. No header was changed.',*out]
    return out


def reference_hints(net, items):
    """Enrich existing signature leads, without widening the search or asserting identity."""
    out=[]
    for item in items:
        item=dict(item)
        fid=item.get('function_id')
        if fid and len(out)<3:
            row=net.store.one("SELECT name,substr(matched_source,1,16000) AS source FROM functions WHERE id=? AND status='matched'",fid)
            if row and row['source']:
                context=declarations(row['source'],row['name'],row['source'])
                if context and len(context)<=3000:
                    item['reference_context']=context
        out.append(item)
    return out


def reference_lines(items):
    out=[]
    for item in items:
        if item.get('reference_context'):
            out += [f"Actual reference declarations/macros from {item['name']} in {item['game']} (not verified here):",
                    '```c',item['reference_context'],'```']
    return out
