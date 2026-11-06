"""Locate control-flow disagreements for review; never synthesize behavioral edits."""
import re

from ..forks.csource import _blank
from .pattern_repairs import close
from .source_lead_analysis import features

MAX_REVIEWS = 4
MAX_CONDITION_BYTES = 1024
MAX_SNIPPET = 300
NEAR_LINES = 3
CONTEXT_RADIUS = 2


def conditions(source):
    clean = _blank(source); result = []
    for hit in re.finditer(r'\b(?:if|while|for|switch)\s*\(',clean):
        end = close(clean,hit.end()-1)
        if end and end-hit.start() <= MAX_CONDITION_BYTES:
            result.append((hit.start(),end,source[:hit.start()].count('\n')+1,
                           ' '.join(source[hit.start():end].split())[:MAX_SNIPPET]))
    return result


def review(source, rows, arch, locations=None):
    source_lines = source.splitlines(); candidates = conditions(source)
    mapped = {}
    for item in (locations or {}).get('lines',[]):
        if type(item.get('line')) is int and 1 <= item['line'] <= len(source_lines):
            for instruction in item.get('instructions',[]):mapped[instruction['row']] = item['line']
    result = []
    for index,row in enumerate(rows or []):
        if len(row) != 3 or row[0] == ' ':continue
        target = features([row[1]],arch); own = features([row[2]],arch)
        control = target['branches'] != own['branches'] or target['calls'] != own['calls']
        if row[0] == 'i' and '?' in row[2] and target['calls'] and own['calls']:continue
        if not control:continue  # branch target relocation alone is not a behavioral difference
        line = mapped.get(index); condition = None
        call = re.fullmatch(r'(?:jal|bal|bl|blx|call|callq)\s+\??([A-Za-z_]\w*)',row[2].strip()) if own['calls'] else None
        if call:
            matches = [item for item in candidates if re.search(r'\b'+re.escape(call[1])+r'\s*\(',_blank(item[3]))]
            if len(matches) == 1:condition = matches[0];line = condition[2]
        if line and condition is None:
            # Show an adjacent actual condition, not a distant unrelated predicate.
            matches = [item for item in candidates if abs(item[2]-line) <= NEAR_LINES]
            if matches:condition = min(matches,key=lambda item:abs(item[2]-line))
        snippet = condition[3] if condition else source_lines[line-1].strip()[:MAX_SNIPPET] if line else ''
        where = f' near source line {line}' if line else ''
        context = [{'row':at,'target':rows[at][1],'candidate':rows[at][2]}
                   for at in range(max(0,index-CONTEXT_RADIUS),min(len(rows),index+CONTEXT_RADIUS+1))]
        message = ('Control-flow review'+where+': '+(snippet+'; ' if snippet else '')+
                   'target '+(row[1] or '(no instruction)')+'; candidate '+(row[2] or '(no instruction)')+
                   '. Check retail/source revision, inlining and compiler profile before broad permutation. No behavioral edit was made.')
        if any(item['source_line']==line and item['condition']==snippet and line for item in result):continue
        result.append({'row':index,'source_line':line,'condition':snippet,'instructions':context,'message':message})
        if len(result) >= MAX_REVIEWS:break
    return result
