"""Join complete GCC allocation dumps within one compilation; never infer original C names."""
import re

from . import rtl_value_trace

LIMIT = 64


def summarize(texts, name, arch=None):
    sections = {phase:rtl_value_trace.function(text,name) for phase,text in texts.items()}
    greg, lreg = sections.get('greg',''), sections.get('lreg','')
    order = re.search(r'^;; \d+ regs to allocate:\s*([^\n]*)',greg,re.M)
    positions = list(map(int,re.findall(r'\d+',order[1]))) if order else []
    dispositions = {int(a):int(b) for a,b in re.findall(r'\b(\d+)\s+in\s+(\d+)\b',
                    greg.split(';; Hard regs used:',1)[0])}
    trace = rtl_value_trace.summarize(texts,name,arch)
    values = {v['pseudo']:v for v in trace['values']}
    locals_ = {}
    for m in re.finditer(r'^Register (\d+) used (\d+) times across (\d+) insns([^\n]*)',lreg,re.M):
        crossed = re.search(r'crosses (\d+) calls?',m[4])
        locals_[int(m[1])] = dict(references=int(m[2]),live_insns=int(m[3]),
                                 calls_crossed=int(crossed[1]) if crossed else 0)
    result = []
    for rank,pseudo in enumerate(positions):
        if pseudo not in dispositions or pseudo not in locals_:
            continue
        item = values.get(pseudo,{})
        life = locals_[pseudo]
        refs, length = life['references'],life['live_insns']
        result.append(dict(pseudo=pseudo,allocation_rank=rank,hard_register=dispositions[pseudo],
                           aliases=item.get('aliases',[]),source_lines=item.get('source_lines',[])[:8],
                           legacy_weight=(refs.bit_length()-1)*refs/length if refs and length else None,
                           **life))
    return dict(values=result[:LIMIT],truncated=len(result)>LIMIT or trace['truncated'],
                note='Actual greg order and dispositions joined to lreg counts within this compilation. '
                     'Legacy weights are advisory; pseudos can represent conversions rather than named C locals.')


def brief(evidence, cycles):
    if not evidence or evidence.get('truncated'):
        return ''
    names = {v.lstrip('$%') for cycle in cycles for v in cycle}
    values = [v for v in evidence.get('values',[]) if names.intersection(v.get('aliases',[]))]
    if not values:
        return ''
    parts = []
    for v in values[:6]:
        lines = ','.join(map(str,v.get('source_lines',[])[:4])) or 'unmapped'
        parts.append(f"pseudo {v['pseudo']} → {v['aliases'][0]}: rank {v['allocation_rank']}, "
                     f"{v['references']} refs/{v['live_insns']} live insns, C lines {lines}")
    return 'GCC allocation evidence (candidate only): '+'; '.join(parts)+'. Internal conversions may share a line; do not assume C-variable identity.'


def priority_changes(before, after):
    """Compare uniquely mapped source-line groups, never pseudo numbers across compilations.

    A line may contain conversions or several variables. These are places to investigate, not C
    identities or causal proofs. Missing/ambiguous mappings deliberately produce no paired claim.
    """
    def indexed(evidence):
        groups = {}
        if not evidence or evidence.get('truncated'):
            return groups
        for value in evidence.get('values', []):
            key = tuple(value.get('source_lines', []))
            if key:
                groups.setdefault(key, []).append(value)
        return {key: values[0] for key, values in groups.items() if len(values) == 1}
    left, right = indexed(before), indexed(after)
    result = []
    for lines in sorted(left.keys() & right.keys()):
        a, b = left[lines], right[lines]
        fields = ('allocation_rank', 'references', 'live_insns', 'hard_register')
        changes = {key: {'before': a.get(key), 'after': b.get(key)} for key in fields if a.get(key) != b.get(key)}
        if changes:
            result.append({'source_lines': list(lines), 'changes': changes,
                           'next_action': 'Inspect this source-line group for copy propagation, value/flag sharing or lifetime changes; try bounded local variants before declaration shuffling.'})
    return result[:6]
