"""Rank CFG-safe scan temporaries by current mapped residuals, without binding C to registers.

Maps are source-hash bound. Residual rows only establish a storage/lifetime hypothesis;
association with a source line is advice, never physical-register or equivalence proof.
"""
from . import allocation_evidence, region_repairs, repair_localisation


def order(source, flow, donors, start, end, rows, locations, arch):
    original=list(donors)
    relevant=repair_localisation.lines(source,locations)
    valid=region_repairs.clean(rows)
    if not relevant or not valid or arch!='mips':return original[:3]
    evidence=allocation_evidence.diagnose(valid,arch)
    if not (evidence['register_map'] or evidence['spill_offsets'] or evidence['register_chains']):return original[:3]
    mapped=[]
    for node in flow.nodes:
        first=source.count('\n',0,node.start)+1
        last=source.count('\n',0,node.end)+1
        if node.start>=end and relevant.intersection(range(first,last+1)):
            mapped.append(node)
    if not mapped:return original[:3]
    def priority(decl):
        writes=[n.start-end for n in mapped if decl.name in n.defines]
        uses=[n.start-end for n in mapped if decl.name in n.uses]
        # A later mapped definition/read marks a phase whose allocation may interact with the
        # scan. CFG deadness at the insertion point was checked by the generating caller.
        return (0 if writes else 1 if uses else 2,min(writes+uses,default=len(source)))
    ranked=sorted(original,key=priority)
    # Keep the old first three probes too; new advice cannot delete their source shapes.
    names=list(dict.fromkeys(d.name for d in ranked[:3]+original[:3]))
    by_name={d.name:d for d in original}
    return [by_name[name] for name in names]
