"""Display/search diagnostics only: categories and fixed-position rendering differences.

Aligned rows can hide large address shifts behind a small numeric score. Neither this
summary nor identical rendered positions is a linked-byte verdict.
"""
import re

BRANCH = re.compile(r'^(?:j[a-z0-9]*|callq?|loop[a-z]*|b|bl|blr|blx|br|bctr|bctrl|cbnz|cbz|tbnz|tbz|'
                    r'b(?:eq|ne|lt|le|gt|ge|c1t|c1f)[a-z0-9+-]*|b\.(?:eq|ne|lt|le|gt|ge|hi|ls|hs|lo|mi|pl))$')


def summarize(rows):
    counts = dict.fromkeys(('opcode', 'register', 'stack', 'branch_target', 'operand',
                            'reordered', 'inserted', 'deleted', 'unknown'), 0)
    left, right = [], []
    for row in rows or []:
        if not isinstance(row, (tuple,list)) or len(row)!=3 or not all(isinstance(v,str)for v in row):
            counts['unknown']+=1
            continue
        marker,target,candidate=row
        if target:left.append(target)
        if candidate:right.append(candidate)
        if marker==' ':continue
        a=target.split(' ',1)[0];b=candidate.split(' ',1)[0]
        key={'r':'register','s':'stack','o':'reordered','>':'inserted','<':'deleted'}.get(marker)
        if key is None:
            key=('opcode' if a and b and a!=b else
                 'branch_target' if marker=='i' and a==b and BRANCH.fullmatch(a) else
                 'operand' if marker in {'i','|'} else 'unknown')
        counts[key]+=1
    comparable=bool(left)and len(left)==len(right)and not counts['unknown']
    return {'counts':counts,'target_instructions':len(left),'candidate_instructions':len(right),
            'position_differences':sum(a!=b for a,b in zip(left,right)) if comparable else None,
            'stack_only':bool(counts['stack'])and sum(counts.values())==counts['stack']}


def text(rows):
    report=summarize(rows)
    detail=', '.join(f'{value} {key.replace("_"," ")}' for key,value in report['counts'].items()if value)
    position=report['position_differences']
    result = ('Mismatch categories (advisory): '+(detail or 'no rendered differences')+
            (f'; {position} rendered instruction positions differ.' if position is not None else '.'))
    hint=advice(rows)
    return result+('\n'+hint if hint else '')


def advice(rows):
    """Suggest a bounded next experiment from rendered evidence, never infer byte equality."""
    report=summarize(rows);counts=report['counts']
    shifted = [(i,row) for i,row in enumerate(rows or []) if len(row)==3 and row[0] in {'>','<'}]
    if len(shifted)==1 and (shifted[0][1][1] or shifted[0][1][2]).strip()=='nop' and counts['branch_target']>=2:
        index,_ = shifted[0]
        earlier = any(len(row)==3 and row[0]!=' ' for row in rows[:index])
        return ('One unmatched NOP accompanies multiple branch-target differences. Inspect '+
                ('the earlier mismatch first' if earlier else 'the NOP scheduling boundary first')+
                '; downstream branch differences may follow an instruction-position shift. '
                'Check local lifetimes and consistent typed global access before changing branches. '
                'This is advisory: linked bytes remain authoritative.')
    if counts['register'] and sum(counts.values())==counts['register']:
        return (f"Only {counts['register']} rendered register differences remain. Try allocation/address staging "
                "and existing-load qualification with try_local_repairs; compare debug-flag variants through "
                "compiler_experiments. These are hypotheses; the exact judge still decides.")
    return ''
