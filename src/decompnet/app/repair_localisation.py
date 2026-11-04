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
    return dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(),mapping=compact)


def rebase(source, extracted, mapping):
    """A unique literal function substring can be located in a submitted file; no fuzzy alignment."""
    if not extracted or source.count(extracted)!=1:return None
    offset=source[:source.index(extracted)].count('\n')
    adjusted={**mapping,'lines':[dict(line=e['line']+offset) for e in mapping.get('lines',[]) if type(e.get('line')) is int]}
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
    return list(dict.fromkeys(result))
