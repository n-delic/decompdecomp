"""Recover a single-entry conditional back edge, including switch-heavy loop bodies.

The CFG must account for every label and jump. Existing nested breaks/continues must keep
their destination inside the new loop; no trip count, entry test or update is invented.
"""
import re

from . import local_flow
from .integer_repair_support import IDENT, edit
from .pattern_repairs import close


def variants(source, name):
    flow = local_flow.analyze(source,name)
    if not flow:
        return []
    part = flow.part
    result = []
    for label, index in flow.labels.items():
        entry = flow.nodes[index]
        if part.clean[part.opening+1:entry.start].rstrip()[-1:] not in {'', ';', '{', '}'}:
            continue
        jumps = [(i,key) for i,key in flow.gotos if key==label]
        if len(jumps)!=1:
            continue
        jump = flow.nodes[jumps[0][0]]
        if jump.start<=entry.end:
            continue
        # Find the exact latch statement containing this one goto.
        for hit in re.finditer(r'\bif\s*\(',part.clean[entry.end:jump.start]):
            start=entry.end+hit.start()
            opening=entry.end+hit.end()-1
            end=close(part.clean,opening)
            if not end:
                continue
            body=flow.skip(end,part.end)
            stop=flow.statement_end(body,part.end)
            text=part.clean[body:stop].strip()
            if not re.fullmatch(rf'(?:{{\s*)?goto\s+{label}\s*;\s*(?:}})?',text):
                continue
            if re.match(r'\s*else\b',part.clean[stop:]):
                continue
            inner=part.clean[entry.end:start]
            if inner.count('{')!=inner.count('}'):
                continue
            # Do not introduce a shorter scope for a VLA, escaped local or C++ object.
            # The motivating shape declares its locals before the back-edge label.
            if any(m[1] not in {'goto','return','case','break','continue'} for m in re.finditer(
                    rf'(?<=[;{{}}])\s*(?:struct\s+)?({IDENT})\s+\**\s*{IDENT}\s*(?:[;=,\[])', ';'+inner)):
                continue
            # An outside jump into the new scope would create another entry. Exiting gotos
            # keep their original destinations and are safe in these ordinary C blocks.
            safe=True
            for i,key in flow.gotos:
                target=flow.nodes[flow.labels[key]]
                origin=flow.nodes[i]
                if not entry.start<=origin.start<stop and entry.start<=target.start<stop:
                    safe=False
            for node in flow.nodes:
                if entry.end<=node.start<start and re.match(r'\s*(?:break|continue)\b',part.clean[node.start:node.end]):
                    if not node.successors or any(not entry.end<=flow.nodes[n].start<start for n in node.successors):
                        safe=False
            # A case belonging to an enclosing switch would be another loop entry.
            for match in re.finditer(r'\b(?:case\s+[^:]+|default)\s*:',inner):
                pos=entry.end+match.start()
                # Verify nested switch containment using its lexical statement extent.
                contained=False
                for switch in re.finditer(r'\bswitch\s*\(',inner[:match.start()]):
                    sw=entry.end+switch.start()
                    if flow.statement_end(sw,part.end)>pos:
                        contained=True
                if not contained:
                    safe=False
            if not safe:
                continue
            # The condition is evaluated once at exactly the old latch position.
            replacement='do {'+source[entry.end:start]+'} while ('+source[opening+1:end-1]+');'
            result.append(('structured_backedge',edit(source,[(entry.start,stop,replacement)])))
            if len(result)>=4:
                return result
    return result
