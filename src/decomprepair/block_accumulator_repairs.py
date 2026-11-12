"""Split independent scalar accumulators at existing lexical block boundaries."""
import re

from . import local_flow
from .integer_lifetime_repairs import depth, jump_crosses
from .integer_repair_support import DECL, edit, refs, view
from .pattern_repairs import close

FAMILIES = ('block_accumulator_lifetime',)

def independent(text, name, end):
    assignment = re.match(r'\s*=(?!=)([^;{}]*);',text[end:])
    return assignment and not re.search(r'\b'+re.escape(name)+r'\b',assignment[1])


def variants(source, name):
    part = view(source,name)
    if not part:
        return []
    declarations = [d for d in DECL.finditer(part.clean,part.opening+1,part.end)
                    if depth(part.clean,d.start()) == depth(part.clean,part.opening)+1
                    and not re.search(r'&\s*(?:\(\s*)*'+re.escape(d['name'])+r'\b',part.body)]
    result = []
    for opening in (m.start() for m in re.finditer('{',part.clean[part.opening+1:part.end-1])):
        opening += part.opening+1
        end = close(part.clean,opening,'{','}')
        if not end or jump_crosses(part,opening,end):
            continue
        edits, added = [], []
        for decl in declarations:
            # Other sibling blocks may already shadow this outer local. Never rewrite a block
            # containing its own declaration; its binding belongs to that declaration instead.
            if re.search(r'\b(?:[su](?:8|16|32)|int|char|short|long|float|double|volatile|const)\s+[^;{}]*\b'+re.escape(decl['name'])+r'\s*[;=,]',part.clean[opening+1:end-1]):
                continue
            hits = refs(part,decl)
            inner = [h for h in hits if opening<h.start()<end]
            later = [h for h in hits if h.start()>=end]
            if (not inner or depth(part.clean,inner[0].start()) != depth(part.clean,opening)+1
                    or re.search(r'\b(?:if|for|while|switch|goto|return)\b',part.clean[opening+1:inner[0].start()])
                    or not independent(part.clean,decl['name'],inner[0].end())):
                continue
            # The old value must be dead both at entry and exit. Never change a loop-carried update.
            if later:
                # Textual assignment order cannot prove death across a jump or loop edge.
                # The CFG deliberately excludes shadowed locals, so demand a tracked binding.
                flow = local_flow.analyze(source,name)
                if not flow or decl['name'] not in flow.names:
                    continue
                edges = [flow.nodes[n] for node in flow.nodes if opening<node.start<end
                         for n in node.successors if not opening<flow.nodes[n].start<end]
                if not edges or any(decl['name'] in node.live for node in edges):
                    continue
            if not any(re.match(r'\s*(?:\+=|-=|\*=|/=)',part.clean[h.end():]) for h in inner[1:]):
                continue
            fresh = '__block_'+decl['name']
            if re.search(r'\b'+fresh+r'\b',part.clean):
                continue
            added.append(f'{decl["type"]} {fresh};')
            edits.extend((h.start(),h.end(),fresh) for h in inner)
        if added:
            edits.append((opening+1,opening+1,'\n'+ '\n'.join(added)+'\n'))
            result.append(('block_accumulator_lifetime',edit(source,edits)))
    return result[:6]
