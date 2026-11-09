"""Private, profile-scoped expression templates learned only after an ordinary exact verdict.

Identifier slots bind existing names; recipes cannot introduce calls, names, literals or
control flow. These are hypotheses, never acceptance rules. The normal judge tests them.
"""
import difflib
import hashlib
import json
import re
from collections import Counter

from .repair_tokens import tokens

SCHEMA = '''CREATE TABLE IF NOT EXISTS expression_recipes(
 profile TEXT NOT NULL, recipe TEXT NOT NULL, successes INTEGER NOT NULL,
 PRIMARY KEY(profile,recipe))'''
TYPES = {'unsigned','signed','int','short','long','char','s8','s16','s32','u8','u16','u32'}
KEYWORDS = TYPES | set('return if else for while do switch case default break continue goto sizeof typedef struct union enum void float double const volatile static extern register auto inline restrict _Bool'.split())
OPERATORS = {'(',')','+','-','*','/','%','&','|','^','~','!','<<','>>','<','>','<=','>=','==','!='}


def significant(source):
    return [t for t in tokens(source) if not t.isspace() and not t.startswith(('/*','//'))]


def template(before, after):
    """Capture one small expression edit with unchanged lexical context on both sides."""
    if '#' in before+after or re.search(r'\b(?:asm|__asm__)\b',before+after):
        return None
    left,right=significant(before),significant(after)
    def calls(sequence):
        return Counter(token for token,following in zip(sequence,sequence[1:])
                       if re.fullmatch(r'[A-Za-z_]\w*',token) and token not in TYPES and following=='(')
    if calls(right)-calls(left):
        return None
    edits=[x for x in difflib.SequenceMatcher(None,left,right,autojunk=False).get_opcodes() if x[0]!='equal']
    if not edits:
        return None
    a,c=edits[0][1],edits[0][3]
    b,d=edits[-1][2],edits[-1][4]
    if max(b-a,d-c)>8 or a<2 or len(left)-b<2:
        return None
    old,new=left[a:b],right[c:d]
    for sequence in (old,new):
        if any(re.fullmatch(r'[A-Za-z_]\w*',token) and token not in TYPES and following=='('
               for token,following in zip(sequence,sequence[1:])):
            return None
    # Existing operands may reorder; only scalar casts/operators may be introduced.
    if any(t not in old and t not in TYPES|OPERATORS for t in new):
        return None
    if any(t in {'{','}',';',',','=','++','--','->','[',']'} or t.startswith(('"',"'")) for t in old+new):
        return None
    names={}
    def encode(seq):
        result=[]
        for t in seq:
            if re.fullmatch(r'[A-Za-z_]\w*',t) and t not in KEYWORDS:
                if t not in names:
                    names[t]=f'${len(names)}'
                t=names[t]
            result.append(t)
        return result
    context=min(a,8)
    pattern=encode(left[a-context:b+8])
    replacement=encode(new)
    return {'pattern':pattern,'replacement':replacement,'start':context,'length':b-a}


def learn(net, profile, before, after):
    recipe=template(before,after)
    if not recipe:
        return
    with net.store.tx() as db:
        db.execute(SCHEMA)
        db.execute('''INSERT INTO expression_recipes VALUES(?,?,1) ON CONFLICT(profile,recipe)
                      DO UPDATE SET successes=successes+1''',(profile,json.dumps(recipe,sort_keys=True)))


def apply(source, recipe):
    """Require unique context and consistent identifier bindings; keep all other source intact."""
    parts=tokens(source)
    positions=[i for i,t in enumerate(parts) if not t.isspace() and not t.startswith(('/*','//'))]
    pattern=recipe['pattern']; hits=[]
    for start in range(len(positions)-len(pattern)+1):
        bindings={};valid=True
        for offset,wanted in enumerate(pattern):
            actual=parts[positions[start+offset]]
            if wanted.startswith('$'):
                if (not re.fullmatch(r'[A-Za-z_]\w*',actual) or actual in KEYWORDS
                        or actual != bindings.setdefault(wanted,actual)):
                    valid=False;break
            elif wanted!=actual:
                valid=False;break
        if valid and len(set(bindings.values()))==len(bindings):
            hits.append((start,bindings))
    if len(hits)!=1:
        return None
    start,bindings=hits[0]; at=start+recipe['start'];length=recipe['length']
    begin=positions[at];end=positions[at+length-1]+1 if length else begin
    replacement=' '.join(bindings.get(t,t) for t in recipe['replacement'])
    return ''.join(parts[:begin])+replacement+''.join(parts[end:])


def variants(net, profile, source):
    with net.store.tx() as db:
        db.execute(SCHEMA)
    rows=net.store.all('SELECT recipe FROM expression_recipes WHERE profile=? ORDER BY successes DESC LIMIT 16',profile)
    result=[]
    for row in rows:
        candidate=apply(source,json.loads(row['recipe']))
        if candidate and candidate!=source and candidate not in result:
            result.append(candidate)
    return result


def signature(net, profile):
    """New recipes invalidate a completed iterative frontier, without retrying old misses."""
    with net.store.tx() as db:
        db.execute(SCHEMA)
    rows=net.store.all('SELECT recipe FROM expression_recipes WHERE profile=? ORDER BY recipe',profile)
    return hashlib.sha256(repr([r['recipe'] for r in rows]).encode()).hexdigest()
