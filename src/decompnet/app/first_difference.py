"""Rank edits by explicit operands around the first mismatch; never invent a source map."""
import difflib
import re

from ..forks.csource import _blank

TOKEN = re.compile(r'0[xX][0-9a-fA-F]+|\b\d+\b|\b[A-Za-z_]\w*\b')


def clues(rows):
    rows=[r for r in rows or [] if isinstance(r,(list,tuple)) and len(r)==3 and all(isinstance(x,str) for x in r)]
    first=next((i for i,r in enumerate(rows) if r[0]!=' '),None)
    if first is None:return set()
    result=set()
    for marker,left,right in rows[max(0,first-1):first+5]:
        if marker==' ':continue
        for text in (left,right):
            text=re.sub(r'\$\w+','',text)
            operands=text.split(' ',1)[1] if ' ' in text else ''
            for token in TOKEN.findall(operands):
                result.add(str(int(token,0 if token.lower().startswith('0x') else 10)) if token[0].isdigit() else token)
    return result


def order(source, proposals, rows):
    """Reserve every fourth slot for ordinary exploration, retaining every hypothesis."""
    evidence=clues(rows)
    if not evidence:return proposals
    before=_blank(source).splitlines()
    def score(item):
        after=_blank(item[1]).splitlines()
        changed=[]
        for tag,a,b,c,d in difflib.SequenceMatcher(None,before,after,autojunk=False).get_opcodes():
            if tag!='equal':changed.extend(before[a:b]+after[c:d])
        tokens=TOKEN.findall(' '.join(changed))
        tokens={str(int(t,0 if t.lower().startswith('0x') else 10)) if t[0].isdigit() else t for t in tokens}
        return len(tokens&evidence)
    ranked=sorted(enumerate(proposals[:128]),key=lambda p:-score(p[1]))
    remaining=dict(enumerate(proposals));result=[]
    for index,item in ranked:
        if index not in remaining:continue
        result.append(remaining.pop(index))
        if len(result)%4==3 and remaining:
            ordinary=next(iter(remaining));result.append(remaining.pop(ordinary))
    result.extend(item for index,item in remaining.items())
    return result
