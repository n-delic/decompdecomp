"""Bounded pointer, scheduling and terminal-guard shapes from verified captures.

These propose source candidates, never object edits or verdicts. Every candidate still needs the
ordinary exact judge. Unknown aliases, escaped locals and complex control flow are left alone.
"""
import re
from dataclasses import dataclass

from . import loop_liveness
from .integer_repair_support import IDENT, INTEGER, NUMBER, edit, locals_for, refs, view
from .pattern_repairs import close

LIMIT = 16
POINTER = re.compile(rf'\b(?P<type>(?:void|[su](?:8|16|32)|char|short|int)\s*\*+)\s*(?P<name>{IDENT})\s*;')


@dataclass
class Local:
    name: str
    type: str
    first: int
    last: int
    def start(self):return self.first
    def end(self):return self.last
    def __getitem__(self,key):return getattr(self,key)
    value = None


def integer_locals(part):
    result={d['name']:d for d in locals_for(part)}
    for decl in re.finditer(rf'\b(?P<type>{INTEGER})\s+(?P<names>{IDENT}(?:\s*,\s*{IDENT})+)\s*;',part.clean,):
        if not part.opening<decl.start()<part.end:continue
        for variable in re.findall(IDENT,decl['names']):
            if not re.search(rf'&\s*{variable}\b',part.body):
                result[variable]=Local(variable,decl['type'],decl.start(),decl.end())
    # A later shadow, including a pointer shadow, prevents using this declaration as evidence.
    counts={name:0 for name in result}
    for group in re.finditer(rf'\b(?:{INTEGER}|void|float|double)\s+([^;{{}}()]+);',part.body):
        for item in group[1].split(','):
            hit=re.match(r'\s*\**\s*('+IDENT+r')\b',item)
            if hit and hit[1] in counts:counts[hit[1]]+=1
    return {name:decl for name,decl in result.items() if counts[name]==1}


def variants(source, name):
    part = view(source,name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|goto)\b',part.body):
        return []
    result = []
    def add(family, edits):
        text = edit(source,edits)
        if text!=source and (family,text) not in result and len(result)<LIMIT:
            result.append((family,text))
    integers = integer_locals(part)
    # Swap independent scalar initializations, never loads, calls, increments or stores through pointers.
    scalar = rf'(?:-?\s*{NUMBER}|{IDENT})'
    pair = re.compile(rf'\b(?P<a>{IDENT})\s*=\s*(?P<x>{scalar})\s*;(?P<gap>\s*)'
                      rf'(?P<b>{IDENT})\s*=\s*(?P<y>{scalar})\s*;')
    for hit in pair.finditer(part.clean,part.opening+1,part.end):
        a,b = hit['a'],hit['b']
        if a==b or a not in integers or b not in integers or any(integers[n].end()>hit.start() for n in (a,b)):
            continue
        if part.clean[part.opening+1:hit.start()].rstrip()[-1:] not in {';','{','}'}:
            continue
        if re.search(rf'\b{a}\b',hit['y']) or re.search(rf'\b{b}\b',hit['x']):
            continue
        if any(not re.fullmatch(rf'-?\s*{NUMBER}',value) and value.strip() not in integers for value in (hit['x'],hit['y'])):
            continue
        split = part.clean.index(';',hit.start())+1
        second = hit.start('b')
        add('independent_assignment_order',[(hit.start(),hit.end(),source[second:hit.end()]+source[split:second]+source[hit.start():split])])
    # A pointer chase may want to advance its existing cursor for the first load, then copy to
    # the differently typed chain local. Only the cursor's last read can be replaced this way.
    pointers = {d['name']:d for d in POINTER.finditer(part.clean,part.opening+1,part.end)}
    for chain,decl in pointers.items():
        for cursor,other in pointers.items():
            if chain==cursor or re.search(rf'&\s*(?:{chain}|{cursor})\b',part.body):
                continue
            if any(len(re.findall(rf'\b(?:{INTEGER}|void)\s*\*+\s*{n}\b',part.body))!=1 for n in (chain,cursor)):
                continue
            chase = re.compile(rf'\b{chain}\s*=\s*\*\(\s*[^();]+\*\s*\)\s*\(\(\s*(?:char|u8)\s*\*\s*\)\s*{cursor}\s*\+\s*{NUMBER}\s*\)\s*;\s*'
                               rf'{chain}\s*=\s*\*\(\s*[^();]+\*\s*\)\s*{chain}\s*;')
            for hit in chase.finditer(part.clean,max(decl.end(),other.end()),part.end):
                if part.clean[part.opening+1:hit.start()].rstrip()[-1:] not in {';','{','}'}:
                    continue
                if any(h.start()>=hit.end() for h in refs(part,other)):
                    continue
                local=Local(cursor,other['type'],other.start(),other.end())
                uses=refs(part,other)
                if not uses or not loop_liveness.final_reuse(part,local,uses[-1]):continue
                text = source[hit.start():hit.end()]
                text = re.sub(r'^'+chain+r'\b',cursor,text)
                text = re.sub(r'(?<=\*)\([^()]+\*\s*\)',f'({other["type"]} *)',text,count=1)
                last = list(re.finditer(rf'\b{chain}\b',text))[-1]
                text = text[:last.start()]+cursor+text[last.end():]
                add('pointer_chase_reuse',[(hit.start(),hit.end(),text)])
    # Terminal negative guards can become an early return. A preceding boolean copy may be
    # removed only when it has exactly this assignment and conditional as its entire lifetime.
    for guard in re.finditer(r'\bif\s*\(',part.body):
        start=part.opening+1+guard.start(); condition_start=part.opening+1+guard.end()
        if part.clean[part.opening+1:start].rstrip()[-1:] not in {';','{','}'}:continue
        condition_end=close(part.clean,condition_start-1)
        opening=re.match(r'\s*{',part.clean[condition_end:]) if condition_end else None
        if not opening:continue
        block=condition_end+opening.end()-1; end=close(part.clean,block,'{','}')
        tail=re.match(rf'\s*return\s+(?P<value>{IDENT}|{NUMBER})\s*;',part.clean[end:]) if end else None
        if not tail or part.clean[end+tail.end():part.end].strip().strip('}').strip():continue
        condition=part.clean[condition_start:condition_end-1].strip()
        old_start=start
        boolean=re.fullmatch(rf'(?P<var>{IDENT})\s*!=\s*0',condition)
        if boolean and boolean['var'] in integers:
            variable=boolean['var']
            copy=re.search(rf'\b{variable}\s*=\s*\(\s*(?P<p>{IDENT})\s*==\s*0\s*\)\s*;\s*$',part.clean[:start])
            if copy and len(refs(part,integers[variable]))==2:
                condition='!'+copy['p'];old_start=copy.start()
        negative=re.fullmatch(rf'!\s*(?P<p>{IDENT})|(?P<q>{IDENT})\s*==\s*0',condition)
        if not negative:continue
        arm=part.clean[block+1:end-1]
        if re.search(r'\b(?:goto|case|default|for|while|do|break|continue)\b|(?<!:):(?!:)',arm):continue
        value=tail['value']
        returned=value
        scope=part.clean.rfind('{',part.opening,old_start)+1
        for initialized in re.finditer(rf'\b{INTEGER}\s+(?P<local>{IDENT})\s*=\s*{re.escape(value)}\s*;',part.clean[scope:old_start]):
            local=initialized['local']
            if len(re.findall(rf'\b{local}\s*=(?!=)',part.body))==1:
                returned=local;break
        replacement=f'if ({negative["p"] or negative["q"]}) return {returned};'+source[block+1:end-1]+source[end:end+tail.end()]
        add('terminal_early_return',[(old_start,end+tail.end(),replacement)])
    # A validated random capture repeated a later member assignment before adjacent stores.
    # Keep the original assignment, evaluate only a local/literal RHS, and cross no control/call.
    store=re.compile(rf'(?P<base>{IDENT}|\(\s*&\s*{IDENT}\s*\))->(?P<field>{IDENT})\s*(?P<op>\|=|&=|=)\s*(?P<value>{IDENT}|{NUMBER})\s*;')
    stores=list(store.finditer(part.clean,part.opening+1,part.end))
    signature=re.search(rf'\b{re.escape(name)}\s*\(([^()]*)\)\s*$',part.clean[:part.opening])
    parameters=set(re.findall(rf'\b{INTEGER}\s+({IDENT})\b',signature[1])) if signature else set()
    for index,original in enumerate(stores):
        if original['op']!='=':continue
        if re.fullmatch(IDENT,original['value']) and original['value'] not in parameters|integers.keys():continue
        if part.clean[part.opening+1:original.start()].rstrip()[-1:] not in {'',';','{','}'}:continue
        for following in stores[index+1:index+5]:
            gap=part.clean[original.end():following.end()]
            if original['base']!=following['base'] or original['field']==following['field']:continue
            if re.sub(store,'',gap).strip() or any(s['base']!=original['base'] or s['field']==original['field'] for s in store.finditer(gap)):continue
            add('member_store_replay',[(following.end(),following.end(),'\n'+source[original.start():original.end()])])
    return result
