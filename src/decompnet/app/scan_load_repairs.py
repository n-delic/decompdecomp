"""Capture a repeated local-array scan value once, then try a CFG-dead existing temporary.

Only plain integer arrays and effect-free indices qualify. No volatile or pointer loads, calls,
shared declarations or assembly are rewritten. Compiler/judge evidence decides whether staging helps.
"""
import re

from . import copy_lifetime_repairs, integer_abi
from .integer_repair_support import IDENT, INTEGER, NUMBER, edit
from .pattern_repairs import close

FAMILIES = ('scan_load_capture', 'scan_load_reuse')
LIMIT = 12
ARRAY = re.compile(rf'\b(?P<type>{INTEGER})\s+(?P<name>{IDENT})\s*\[\s*[1-9][0-9]*\s*\]\s*;')


def arrays(source, part):
    scopes = [(i,close(part.clean,i,'{','}')) for i in range(part.opening,part.end) if part.clean[i]=='{']
    def scope_end(start):
        return max((entry for entry in scopes if entry[1] and entry[0]<start<entry[1]),default=(0,0))[1]
    duplicates=set()
    def add(name, kind, start, stop):
        if name in found:duplicates.add(name)
        found[name]=(kind,start,stop)
    records = []
    found = {}
    for record in re.finditer(rf'\bstruct\s*{{([^{{}}]+)}}\s*({IDENT})\s*;',part.body):
        begin=part.opening+1+record.start();end=part.opening+1+record.end()
        records.append((begin,end))
        for decl in ARRAY.finditer(record[1]):
            for alias in re.finditer(rf'^\s*#define\s+({IDENT})\s+{record[2]}\.{decl["name"]}\s*$',source,re.M):
                add(alias[1],decl['type'],end,scope_end(begin))
    for decl in ARRAY.finditer(part.clean,part.opening+1,part.end):
        if not any(a<decl.start()<b for a,b in records):add(decl['name'],decl['type'],decl.end(),scope_end(decl.start()))
    return {name:value for name,value in found.items() if name not in duplicates}


def variants(source, name, header='', arch='mips'):
    if re.search(r'\b__LINE__\b|^\s*#\s*line\b',header+'\n'+source,re.M):return []
    ctx=copy_lifetime_repairs.context(source,name)
    if not ctx:return []
    part,flow,locals_=ctx
    declared=arrays(source,part)
    if not declared:return []
    result=[]
    for branch in re.finditer(r'\bif\s*\(',part.body):
        begin=part.opening+1+branch.start()
        known={n:t for n,(t,a,b) in declared.items() if a<=begin<b and integer_abi.scalar(t,header+'\n'+source,arch) in {(32,True),(32,False)}}
        opening=part.opening+1+branch.end()-1
        cond_end=close(part.clean,opening)
        if not cond_end:continue
        condition=part.clean[opening+1:cond_end-1]
        if '||' in condition:continue
        guard=''
        offset=0
        if '&&' in condition:
            offset=condition.rfind('&&')+2
            guard=condition[:offset-2].strip()
        compare=re.fullmatch(rf'\s*(.+?)\s*(<=|>=|<|>)\s*({IDENT})\s*',condition[offset:])
        if not compare or compare[3] not in locals_:continue
        expression=compare[1].strip();array=re.match(rf'({IDENT})\s*\[',expression)
        if not array or array[1] not in known:continue
        if not re.fullmatch(r'[A-Za-z_0-9\s+\-\[\]]+',expression) or '++' in expression or '--' in expression:continue
        dependencies=set(re.findall(rf'\b{IDENT}\b',expression))
        if not dependencies <= known.keys() | locals_.keys():continue
        if any(integer_abi.scalar(locals_[n].type,header+'\n'+source,arch) not in {(32,True),(32,False)} for n in dependencies-known.keys()):continue
        body_open=cond_end
        while body_open<part.end and part.clean[body_open].isspace():body_open+=1
        if part.clean[body_open:body_open+1]!='{':continue
        end=close(part.clean,body_open,'{','}')
        if not end or re.match(r'\s*else\b',part.clean[end:]):continue
        body=part.clean[body_open+1:end-1]
        assignments=list(re.finditer(rf'\s*({IDENT})\s*=(?!=)\s*([^;{{}}]+)\s*;',body))
        if not assignments or ''.join(m[0] for m in assignments).strip()!=body.strip():continue
        if any(m[1] not in locals_ or m[1] in dependencies or not re.fullmatch(rf'{IDENT}|{NUMBER}|{re.escape(expression)}',m[2].strip()) for m in assignments):continue
        if not any(m[1]==compare[3] and m[2].strip()==expression for m in assignments):continue
        fresh='__repair_scan_value'
        if re.search(rf'\b{fresh}\b',header+'\n'+source):continue
        begin=part.opening+1+branch.start()
        original=source[begin:end]
        positions=[(opening+1+offset+compare.start(1),opening+1+offset+compare.end(1))]
        if guard:positions.append((opening+1,opening+1+offset))
        positions += [(body_open+1+m.start(2),body_open+1+m.end(2)) for m in assignments if m[2].strip()==expression]
        def capture(variable, declaration, positions=positions, begin=begin, end=end, original=original, expression=expression, guard=guard, opening=opening, offset=offset):
            changes=[(a-begin,b-begin,'' if guard and a==opening+1 and b==opening+1+offset else variable) for a,b in positions]
            changed=edit(original,changes)
            return edit(source,[(begin,end,('if('+guard+')' if guard else '')+'{'+declaration+variable+'='+expression+';'+changed+'}')])
        result.append(('scan_load_capture',capture(fresh,known[array[1]]+' ')))
        reusable=[d for d in locals_.values() if d.name not in dependencies | {compare[3]}
                  and d.type==known[array[1]] and d.end()<=begin<d.scope_stop
                  and not re.search(rf'\b{d.name}\b',original) and flow.dead(d.name,opening+1)]
        # A nearby later overwrite is evidence of a temporary used in consecutive phases.
        def next_write(decl, end=end):
            hit=re.search(rf'\b{decl.name}\s*=(?!=)',part.clean[end:])
            return hit.start() if hit else len(source)
        for decl in sorted(reusable,key=next_write)[:3]:result.append(('scan_load_reuse',capture(decl.name,'')))
        if len(result)>=LIMIT:break
    return result[:LIMIT]
