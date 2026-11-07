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


KEY='repair-evidence-sampling'
DEFAULTS={'enabled':True,'automatic_limit':1,'manual_limit':3}


def options(value):
    if not isinstance(value,dict):raise ValueError('Repair evidence settings must be an object.')
    result={key:value.get(key,default) for key,default in DEFAULTS.items()}
    if type(result['enabled']) is not bool:
        raise ValueError('Repair evidence sampling must be enabled or disabled.')
    for key in ('automatic_limit','manual_limit'):
        if type(result[key]) is not int or not 0<=result[key]<=1000:
            raise ValueError('Choose 0–1000 retained repair examples per pattern.')
    return result


def read_options(db):
    sql='SELECT value FROM settings WHERE key=?'
    row=db.one(sql,KEY) if hasattr(db,'one') else db.execute(sql,(KEY,)).fetchone()
    return options(json.loads(row['value'])) if row else dict(DEFAULTS)


def save(store,value):
    value=options(value)
    with store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(KEY,json.dumps(value)))
    return value


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


def retain(db,evidence,before_source,source,now, *, policy=None):
    signature,automated=bucket(evidence,before_source,source)
    if signature is None:return True
    # Keep one installed-rule regression case, or three manual examples for diversity.
    policy=read_options(db) if policy is None else policy
    limit=policy['automatic_limit' if automated else 'manual_limit']
    row=db.execute('SELECT retained FROM repair_capture_groups WHERE signature=?',(signature,)).fetchone()
    keep=not policy['enabled'] or (row['retained'] if row else 0)<limit
    db.execute('INSERT INTO repair_capture_groups VALUES(?,1,?,?,?) ON CONFLICT(signature) DO UPDATE SET '
               'successes=successes+1,retained=retained+excluded.retained,last_at=excluded.last_at',
               (signature,int(keep),now,now))
    return keep
