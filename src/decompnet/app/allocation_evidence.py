"""Allocator and scheduling clues from displayed MIPS rows, never a match verdict."""
import re
from collections import Counter

from . import permuter_focus

REGISTER = re.compile(r'\$[A-Za-z0-9]+\b')


def diagnose(rows, arch=None):
    rows = permuter_focus.normalized(rows)
    from ..arch import get
    reader=get(arch or "mips").repair_search_hints
    hints=reader(rows) if reader else {}
    valid = all(isinstance(r,(list,tuple)) and len(r)==3 and all(isinstance(c,str) for c in r) for r in rows)
    rows = [r for r in rows if isinstance(r,(list,tuple)) and len(r)==3 and all(isinstance(c,str) for c in r)]
    changed = [r for r in rows if r[0]!=' ']
    mapping, reverse = {}, {}
    coherent = bool(changed) and valid
    for marker,left,right in changed:
        a,b = REGISTER.findall(left),REGISTER.findall(right)
        if marker != 'r' or REGISTER.sub('$reg',left) != REGISTER.sub('$reg',right) or not a or len(a)!=len(b):
            coherent = False
            continue
        for target,candidate in zip(a,b):
            if mapping.get(target,candidate)!=candidate or reverse.get(candidate,target)!=target:
                coherent = False
            mapping[target],reverse[candidate] = candidate,target
    swaps = sorted((a,b) for a,b in mapping.items() if a!=b) if coherent else []
    # Equal instruction multisets suggest movement, not permission to reorder side effects.
    left = Counter(r[1] for r in changed if r[1])
    right = Counter(r[2] for r in changed if r[2])
    scheduling = bool(changed) and left == right
    normalized = lambda side: Counter(REGISTER.sub('$reg', r[side]) for r in changed if r[side])
    allocation_schedule = any(r[0] == 'o' for r in changed) and not scheduling and normalized(1) == normalized(2)
    frame = None
    if arch == 'mips':
        from .stack_layout import diagnose as stack
        frame = stack(arch,rows)
    slots = sorted({(r['target'],r['candidate']) for r in (frame or {}).get('local_operands',[]) if r['delta']})
    calls=lambda side:Counter(m[1] for r in rows for m in [re.match(r'(?:jal|callq?|bl)\s+(\S+)',r[side])] if m)
    target_calls,candidate_calls=calls(1),calls(2)
    target_count=sum(bool(r[1]) for r in rows);candidate_count=sum(bool(r[2]) for r in rows)
    merging=bool(target_count and .8*target_count<=candidate_count<target_count and
                 any(n>=3 and candidate_calls[c]<n for c,n in target_calls.items()))
    copy_near=bool(changed) and sum(r[0]=='>' for r in changed)==1 and all(
        (m=='r' and REGISTER.sub('$reg',a)==REGISTER.sub('$reg',b)) or
        (m=='>' and not a and re.fullmatch(r'move\s+\$\w+,\s*\$\w+',b)) for m,a,b in changed)
    return {'target_instructions':target_count,'candidate_instructions':candidate_count,'possible_tail_merging':merging,'copy_near_miss':copy_near,'register_only': valid and bool(changed) and all(marker=='r' and REGISTER.sub('$reg',left)==REGISTER.sub('$reg',right) for marker,left,right in changed), 'register_map':swaps, 'register_cycles':cycles(swaps), 'register_chains':hints.get('register_chains',[]), 'branch_target':hints.get('branch_target',False), 'delay_slot':hints.get('delay_slot',False), 'scheduling':scheduling, 'allocation_schedule':allocation_schedule,
            'target_frame':(frame or {}).get('target_frame'), 'candidate_frame':(frame or {}).get('candidate_frame'),
            'spill_offsets':slots, 'differences':len(changed)}


def signature(rows, arch=None):
    d = diagnose(rows,arch)
    return (permuter_focus.classify(rows),d['target_frame'],d['candidate_frame'],
            tuple(map(tuple,d['spill_offsets'])),tuple(map(tuple,d['register_map'])),d['scheduling'])


def text(rows, arch=None):
    d = diagnose(rows,arch)
    hints = []
    if d['possible_tail_merging']:
        hints.append(f"{d['candidate_instructions']} candidate / {d['target_instructions']} target instructions with fewer repeated calls; inspect tail merging or missing logic, then try selective branch pointer lifetimes")
    if d['register_only']:
        hints.append(f"{d['target_instructions']} ordered instruction forms agree; {d['differences']} register-only rows; prioritize equal-value lifetimes and bounded loop-weight scopes, retaining distinct allocation alternatives")
    if d['register_cycles']:
        hints.append('closed register cycles '+', '.join(' ↔ '.join(c) for c in d['register_cycles'])+
                     '; keep logic, header, target and compiler profile fixed; inspect narrowing/coalescing and lifetime order, not assembly register renaming')
    if d['register_chains']:
        hints.append('local acyclic register chains '+', '.join(' → '.join(c) for c in d['register_chains'])+'; try bounded source lifetime/declaration repairs, not register substitution')
    if d['branch_target'] or d['delay_slot']:
        hints.append('jump/branch target or delay-slot residual; prioritize source control flow before literal edits')
    if d['register_map']:
        hints.append('consistent register substitution '+', '.join(a+'→'+b for a,b in d['register_map'][:6])+
                     '; try lifetime/split-reuse repairs before broad flag search')
    if d['spill_offsets']:
        hints.append('local stack offsets differ '+', '.join(f'{a:#x}→{b:#x}' for a,b in d['spill_offsets'][:4])+
                     '; explicit stack fields need not reproduce compiler spills')
    if d['target_frame'] is not None:
        hints.append(f"frame {d['target_frame']} target / {d['candidate_frame']} candidate")
    if d['allocation_schedule']:
        hints.append('same displayed operations after register normalization; try paired operand reuse and affine-counter scheduling together')
    if d['scheduling']:
        hints.append('same displayed instruction multiset, different order; try dependency-safe initialization order')
    return 'Allocation clues (advisory): '+'; '.join(hints)+'.' if hints else ''


def plan(rows, arch=None):
    """Small evidence record for agents/replays; categories do not establish a compiler cause."""
    d=diagnose(rows,arch)
    if d['register_only']:
        category='allocation';families=['dead_copy_source','divisor_normalization_copy','divisor_value_scope','divisor_flag_split','regional_load_reuse','later_local_reuse','later_operation_reuse','later_call_reuse','branch_local_reuse','allocation_schedule_bridge','pointer_index_scope','equal_value_increment','loop_weight_scope','paired_operand_reuse']
    elif d['branch_target'] or (d['delay_slot'] and not d['scheduling']):
        category='control';families=['structured_backedge','guard_forward_jump','branch_orientation','loop_update_order','goto_leaf','shared_return_tail']
    elif d['scheduling'] or d['allocation_schedule']:
        category='scheduling';families=['debug_statement_boundary','call_one_shot_scope','debug_initialization_group','debug_constant_initialization','initialization_cluster','paired_operand_reuse','affine_counter_guard']
    elif d['spill_offsets'] or d['target_frame']!=d['candidate_frame']:
        category='stack';families=['spill_normalization','local_declaration_order','stack_padding']
    else:
        category=permuter_focus.classify(rows);families=[]
    if d['register_cycles']:
        families=['pointer_integer_phase_union','paired_integer_lifetimes','split_integer_lifetime','merge_integer_lifetimes']+families
    first=next((i+1 for i,r in enumerate(permuter_focus.normalized(rows)) if r[0]!=' '),None)
    return dict(category=category,first_difference_row=first,changed_rows=d['differences'],
                target_instructions=d['target_instructions'],candidate_instructions=d['candidate_instructions'],
                target_frame=d['target_frame'],candidate_frame=d['candidate_frame'],families=families,
                register_cycles=d['register_cycles'],register_chains=d['register_chains'],
                branch_target=d['branch_target'],delay_slot=d['delay_slot'],
                context='Header/prototype correctness cannot be inferred from assembly rows.',
                verdict='Advisory; register substitution is not permission to rename assembly registers.')


def cycles(mapping):
    """Only closed, bijective substitutions; partial chains and conflicting evidence give no cycle."""
    edges = dict(mapping)
    if len(edges) != len(mapping) or len(set(edges.values())) != len(edges):
        return []
    result, visited = [], set()
    for start in sorted(edges):
        path, node = [], start
        while node in edges and node not in visited and node not in path:
            path.append(node)
            node = edges[node]
        visited.update(path)
        if node == start and len(path) > 1:
            result.append(path)
    return result[:8]
