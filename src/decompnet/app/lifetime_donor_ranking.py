"""Rank safe donor locals using unambiguous same-input compiler source-line evidence only."""
import hashlib
import re


def rank(flow, phase, donors, evidence, source, header):
    """Missing, stale or ambiguous evidence preserves deterministic declaration order.

    Source lines are not C-variable names: accept a future definition only when its line has
    one CFG definition and one captured allocated value. Never infer identity from a register.
    """
    if re.search(r'^\s*#\s*(?:line\b|\d)',source,re.M):return donors
    evidence = evidence or {}
    trace = evidence.get('value_trace') or {}
    allocation = evidence.get('allocation') or {}
    if (trace.get('truncated') or allocation.get('truncated')
            or trace.get('source_sha256')!=hashlib.sha256(source.encode()).hexdigest()
            or trace.get('header_sha256')!=hashlib.sha256(header.encode()).hexdigest()):
        return donors
    values = allocation.get('values') or trace.get('values',[])
    if not isinstance(values,list):return donors
    last = max(flow.nodes[n].end for n in phase)
    # A call-crossing lifetime is closer to the required result than a different measured shape.
    calls = sum('(' in flow.part.clean[flow.nodes[n].start:flow.nodes[n].end]
                and flow.part.clean[flow.nodes[n].start:flow.nodes[n].end].strip().endswith(';') for n in phase)
    definitions = {}
    for node in flow.nodes:
        if node.defines:
            line=source[:node.start].count('\n')+1
            definitions.setdefault(line,[]).append(node)
    scored = {}
    for name in donors:
        scores = []
        for line,nodes in definitions.items():
            if len(nodes)!=1 or nodes[0].start<last or nodes[0].defines!={name}:continue
            matching=[v for v in values if isinstance(v,dict) and line in v.get('source_lines',[])]
            if len(matching)!=1:continue
            value=matching[0];crossed=value.get('calls_crossed');order=value.get('allocation_rank')
            if type(crossed)!=int or crossed<0:continue
            scores.append((abs(crossed-calls),order if type(order)==int and order>=0 else 1000000))
        if len(scores)==1:scored[name]=scores[0]
    return sorted(donors,key=lambda name:(name not in scored,scored.get(name,(1000000,1000000))))
