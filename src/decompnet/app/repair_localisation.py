"""Source-bound compiler-line advice for independently applicable local repairs.

No source location is inferred from instruction position. Whole-function proposals remain
available; a mapped line is an association, not a proof that it causes a mismatch.
"""
import difflib
import hashlib

from ..forks.csource import _blank

LIMIT = 4
# These generators change standalone call statements or independent numeric operands.
# Never split declaration/lifetime/control-flow transformations into disconnected hunks.
INDEPENDENT = {'narrow_call_destination', 'address_or_literal'}


def bind(source, mapping):
    compact=dict(available=bool(mapping and mapping.get('available')),
                 lines=[{'line':e['line']} for e in (mapping or {}).get('lines',[]) if type(e.get('line')) is int])
    registers=(mapping or {}).get('scalar_registers')
    if isinstance(registers,dict):
        compact['scalar_registers']={str(k):v for k,v in registers.items() if str(k).isdigit() and isinstance(v,str)}
    return dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(),mapping=compact)


def rebase(source, extracted, mapping):
    """A unique literal function substring can be located in a submitted file; no fuzzy alignment."""
    if not extracted or source.count(extracted)!=1:return None
    offset=source[:source.index(extracted)].count('\n')
    adjusted={**mapping,'lines':[dict(line=e['line']+offset) for e in mapping.get('lines',[]) if type(e.get('line')) is int]}
    if isinstance(mapping.get('scalar_registers'),dict):
        adjusted['scalar_registers']={str(int(k)+offset):v for k,v in mapping['scalar_registers'].items() if str(k).isdigit()}
    return bind(source,adjusted)


def lines(source, evidence):
    if not isinstance(evidence,dict) or evidence.get('source_sha256')!=hashlib.sha256(source.encode()).hexdigest():return set()
    mapping=evidence.get('mapping') or {}
    if not mapping.get('available'):return set()
    return {e['line'] for e in mapping.get('lines',[]) if isinstance(e,dict) and type(e.get('line')) is int
            and 1<=e['line']<=len(source.splitlines())}


def proposals(source, tagged, evidence):
    """Add at most four mapped, self-contained statement edits, retaining all original proposals."""
    relevant=lines(source,evidence)
    if not relevant:return tagged
    before=source.splitlines(True);local=[]
    for family,text in tagged:
        if family not in INDEPENDENT:continue
        after=text.splitlines(True)
        changes=[op for op in difflib.SequenceMatcher(None,before,after,autojunk=False).get_opcodes() if op[0]!='equal']
        if len(changes)<2:continue
        for _,a,b,c,d in changes:
            if not relevant.intersection(range(a+1,max(a+2,b+1))):continue
            left,right=''.join(before[a:b]),''.join(after[c:d])
            # A complete statement edit only: no brace, label, preprocessor or declaration split.
            clean=_blank(left+right)
            if any(ch in clean for ch in '{}:#') or not left.rstrip().endswith(';') or not right.rstrip().endswith(';'):continue
            item=(family,''.join(before[:a]+after[c:d]+before[b:]))
            if item not in local and item not in tagged:local.append(item)
            if len(local)>=LIMIT:break
        if len(local)>=LIMIT:break
    # Alternate local and existing candidates; ordinary exploration never disappears.
    result=[]
    for i,item in enumerate(tagged):
        if i<len(local):result.append(local[i])
        result.append(item)
    result.extend(local[len(tagged):])
    return order(source,list(dict.fromkeys(result)),evidence)


TARGETED = {'scan_load_capture', 'scan_load_reuse', 'dead_copy_source', 'divisor_normalization_copy', 'divisor_value_scope',
            'divisor_flag_split', 'regional_load_reuse', 'debug_statement_boundary'}


def changed_lines(source, candidate):
    """Bound a complete proposal's changed span without splitting scope/lifetime edits."""
    if source == candidate:
        return set()
    def common(limit, same):
        low, high = 0, limit
        while low < high:
            middle = (low+high+1)//2
            if same(middle):low=middle
            else:high=middle-1
        return low
    size = min(len(source),len(candidate))
    prefix = common(size,lambda n:source[:n]==candidate[:n])
    suffix = common(size-prefix,lambda n:source[len(source)-n:]==candidate[len(candidate)-n:])
    start = source.count('\n',0,prefix)+1
    end = source.count('\n',0,len(source)-suffix)+1
    return set(range(start,end+1))


def order(source, tagged, evidence):
    """Three mapped proposals then one broad probe; unmapped hypotheses always remain available."""
    relevant = lines(source,evidence)
    if not relevant:
        return tagged
    near, broad = [], []
    for item in tagged:
        changed = changed_lines(source,item[1]) if item[0] in TARGETED else set()
        (near if changed and len(changed)<=8 and any(abs(a-b)<=1 for a in changed for b in relevant) else broad).append(item)
    result = []
    while near or broad:
        result.extend(near[:3]); del near[:3]
        if broad:result.append(broad.pop(0))
    return result
