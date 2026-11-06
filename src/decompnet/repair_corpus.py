"""Bound repeated successful captures; signatures are sampling buckets, not proved rules."""
import difflib
import hashlib
import json
import re

from .repair_changes import TOKEN, without_comments
from .repair_review import TYPE_WORDS

AUTOMATIC = {'deterministic draft repair', 'automatic agent local pass', 'deep local repair',
             'random rewrite', 'automatic candidate', 'machine draft'}
WORDS = TYPE_WORDS | set('return if else for while do switch case default break continue goto sizeof static extern const volatile union enum typedef'.split())
SCHEMA = '''CREATE TABLE IF NOT EXISTS repair_capture_groups(
 signature TEXT PRIMARY KEY, successes INTEGER NOT NULL, retained INTEGER NOT NULL,
 first_at REAL NOT NULL, last_at REAL NOT NULL)'''


def bucket(evidence, before_source, source):
    after=evidence['after']
    provenance=evidence.get('provenance') or {}
    automated=provenance.get('kind') in AUTOMATIC and bool(provenance.get('family'))
    # Incomplete baselines/build inputs and unusually large diffs cannot safely be grouped.
    if not before_source or not evidence.get('comparable_settings') or len(before_source)+len(source)>65536:
        return None,automated
    settings=after.get('settings') or {}
    if settings.get('mode')!='rom' or not settings.get('compiler') or not settings.get('context_sha256'):
        return None,automated
    a=TOKEN.findall(without_comments(before_source));b=TOKEN.findall(without_comments(source))
    if len(a)+len(b)>4096:return None,automated
    names={}
    def normalize(tokens):
        result=[]
        for token in tokens:
            if re.fullmatch(r'[A-Za-z_]\w*',token) and token not in WORDS:
                token=names.setdefault(token,'identifier'+str(len(names)))
            result.append(token)
        return result
    changes=[]
    for tag,i,j,k,l in difflib.SequenceMatcher(None,a,b).get_opcodes():
        if tag!='equal':changes.append((tag,normalize(a[i:j]),normalize(b[k:l])))
    if not changes:return None,automated
    # Preserve constants, literals, compiler flags, header/scoring revisions and multi-rule paths.
    # ROM identity, function address and agent identity do not define a repair technique.
    context={k:v for k,v in settings.items() if k not in {'rom','settings_source','context_unit'}}
    key=(context,changes,provenance.get('kind') if automated else 'manual',
         provenance.get('family') if automated else None,provenance.get('path') if automated else None)
    return hashlib.sha256(json.dumps(key,sort_keys=True,default=str).encode()).hexdigest(),automated


def retain(db,evidence,before_source,source,now):
    signature,automated=bucket(evidence,before_source,source)
    if signature is None:return True
    # Keep one installed-rule regression case, or three manual examples for diversity.
    limit=1 if automated else 3
    row=db.execute('SELECT retained FROM repair_capture_groups WHERE signature=?',(signature,)).fetchone()
    keep=row is None or row['retained']<limit
    db.execute('INSERT INTO repair_capture_groups VALUES(?,1,?,?,?) ON CONFLICT(signature) DO UPDATE SET '
               'successes=successes+1,retained=retained+excluded.retained,last_at=excluded.last_at',
               (signature,int(keep),now,now))
    return keep
