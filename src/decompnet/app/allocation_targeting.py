"""Rank a few actual operations through captured GCC priorities; never invent value uses."""
import hashlib
import math

from . import allocation_evidence, loop_weight_repairs, rtl_value_trace
from .value_trace_repairs import changes, touches


def targets(source, header, rows, evidence, arch="mips"):
    allocation = (evidence or {}).get('allocation') or {}
    trace = (evidence or {}).get('value_trace') or {}
    if allocation.get('truncated') or trace.get('truncated'):
        return []
    if trace.get('source_sha256') != hashlib.sha256(source.encode()).hexdigest():return []
    if trace.get('header_sha256') != hashlib.sha256(header.encode()).hexdigest():return []
    mapped = set(rtl_value_trace.focus(trace,source,rows,header))
    if not mapped:return []
    diagnosis = allocation_evidence.diagnose(rows,arch)
    names = {n.lstrip('$%') for cycle in diagnosis['register_cycles'] for n in cycle}
    values = [v for v in allocation.get('values', []) if names.intersection(v.get('aliases', []))]
    result = []
    for value in values:
        refs, length = value.get('references', 0), value.get('live_insns', 0)
        if not isinstance(refs, int) or not isinstance(length, int) or refs <= 0 or length <= 0:continue
        weight = (refs.bit_length()-1)*refs/length
        peers = [p for p in values if p is not value and isinstance(p.get('legacy_weight'), (int, float))
                 and math.isfinite(p['legacy_weight']) and p['legacy_weight'] > weight]
        peer = min(peers, key=lambda p:p['legacy_weight']) if peers else None
        extra = next((n for n in range(1, 7) if ((refs+n).bit_length()-1)*(refs+n)/length > peer['legacy_weight']), None) if peer else None
        lines = [n for n in value.get('source_lines', []) if type(n) is int and n in mapped and 0 < n <= len(source.splitlines())]
        if lines:
            result.append({'pseudo':value['pseudo'], 'aliases':value.get('aliases', [])[:2],
                           'source_lines':lines[:8], 'references':refs, 'live_insns':length,
                           'priority':int(weight*10000), 'next_priority':int(peer['legacy_weight']*10000) if peer else None,
                           'additional_weighted_refs':extra})
    return result[:6]


def proposals(source, name, header, rows, evidence, arch="mips"):
    """Put a source-mapped quotient scope before broad lifetime mutations."""
    points = targets(source, header, rows, evidence, arch)
    if not points:return []
    lines = set(n for p in points if p['additional_weighted_refs'] is not None for n in p['source_lines'])
    needed=min((p['additional_weighted_refs'] for p in points if p['additional_weighted_refs'] is not None),default=1)
    estimated=min(3,max(1,(needed+1)//2))
    depths=tuple(sorted((1,2,3),key=lambda n:abs(n-estimated)))
    candidates = loop_weight_repairs.variants(source, name, header,focus_lines=lines,depths=depths)
    selected = []
    for family, candidate in candidates:
        if touches(source, changes(source, candidate), lines, 0):selected.append((family, candidate))
    return list(dict.fromkeys(selected))[:18]


def brief(source, header, rows, evidence, arch="mips"):
    points = targets(source, header, rows, evidence, arch)
    if not points:return ''
    lines = source.splitlines()
    parts = []
    for point in points[:3]:
        text = (f"{','.join(point['aliases'])}: {point['references']} refs/{point['live_insns']} live, "
                f"priority {point['priority']}")
        if point['additional_weighted_refs'] is not None:
            text += f"; next competing priority {point['next_priority']}, needs at least +{point['additional_weighted_refs']} weighted refs if lifetime stays fixed"
        snippets = [f"L{n}: {lines[n-1].strip()[:140]}" for n in point['source_lines'][:2]]
        parts.append(text+'; '+' / '.join(snippets))
    return ('Targeted allocation brief (candidate evidence, not original C identities): '+'; '.join(parts)+
            '. Next local experiment: try a quotient scope at these mapped lines (including middle assignments); rank depths 1–3 by the approximate +2 weighted refs per nesting level, then verify actual output. This depth estimate is a heuristic, not a measured compiler rule. Counts alone do not override local reservations/conflicts; never add dummy uses.')
