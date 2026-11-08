"""Stage an existing narrow call conversion through a proven-dead, unescaped word local.

The ANSI prototype supplies the conversion; CFG liveness supplies the reusable destination.
No new width, address, alias or compiler setting is guessed. The normal judge decides every candidate.
"""
import re

from . import branch_call_support as support
from . import integer_abi
from .call_lifetime_repairs import block_path
from .integer_repair_support import edit

FAMILY = 'narrow_call_destination'
FAMILIES = (FAMILY,)
LIMIT = 6


def variants(source, name, header='', arch=None):
    facts=support.facts(source,name,header,arch)
    if not facts:return []
    part,flow,scalars=facts;context=header+'\n'+source;groups={}
    nodes=sorted(flow.nodes,key=lambda n:n.start)
    for node in nodes:
        call=support.call(part,node)
        if not call or part.clean[:node.start].rstrip()[-1:] not in ';{}':continue
        _,callee,args=call;prototype=support.prototype(context,callee,arch)
        if not prototype or len(prototype[1])!=len(args):continue
        for index,(argument,formal) in enumerate(zip(args,prototype[1])):
            narrow=integer_abi.scalar(formal,context,arch)
            if not narrow or narrow[0] not in (8,16) or argument not in scalars:continue
            if integer_abi.scalar(scalars[argument],context,arch)[0]!=32:continue
            if any(re.search(rf'\b{re.escape(argument)}\b',a) for i,a in enumerate(args) if i!=index):continue
            for decl in flow.locals:
                donor=decl['name']
                if donor==argument or decl.end()>node.start or donor in node.live:continue
                if narrow[1] and integer_abi.scalar(decl['type'],context,arch)!=(32,True):continue
                if sum(d['name']==donor for d in flow.locals)!=1:continue
                if any(re.search(rf'\b{re.escape(donor)}\b',a) for a in args):continue
                path=block_path(part,decl.start())
                if block_path(part,node.start)[:len(path)]!=path:continue
                # The donor dies before this call (or is killed by its return assignment),
                # so no later use can observe the staged value instead of the original one.
                updated=list(args);updated[index]=donor
                assignment=(f'{call[0]} = ' if call[0] else '')+callee+'('+', '.join(updated)+');'
                replacement=f'{donor} = ({formal}){argument};\n'+assignment
                groups.setdefault((donor,formal),[]).append((node.start,node.end,replacement))
    result=[]
    # Reusing an established scalar from other phases is more likely to retain a global
    # allocation than an otherwise unused scratch. This ranks only proven-safe donors.
    ranked=sorted(groups,key=lambda k:(-len(re.findall(r'\b'+re.escape(k[0])+r'\b',part.body)),k))
    for key in ranked:
        changes=groups[key]
        # One donor can cover independent arms, but never stage two arguments of the same call together.
        seen=set();selected=[]
        for change in changes:
            if change[0] not in seen:selected.append(change);seen.add(change[0])
        result.append((FAMILY,edit(source,selected)))
        if len(result)>=LIMIT:break
    return list(dict.fromkeys(result))
