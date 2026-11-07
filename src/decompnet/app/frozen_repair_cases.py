"""Private multi-case replay inputs, pinned to source, header, target and compiler evidence."""
import hashlib
import json
from pathlib import Path


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def select_holdouts(items, development_functions, *, count=6):
    """Choose distinct functions without looking at candidate results or score gains.

    Freeze the returned inputs before running any policy. This separates the
    current rule's development examples, not every historical human experiment.
    """
    if type(count) is not int or not 2<=count<=16:raise ValueError('Choose 2–16 holdout functions.')
    excluded=set(development_functions)
    names=[i['name'] for i in items]
    if len(names)!=len(set(names)):raise ValueError('Holdouts must use distinct functions, not repeated precursors.')
    eligible=[i for i in items if i['name'] not in excluded]
    if len(eligible)<count:raise ValueError('Not enough independent functions after development exclusions.')
    return sorted(eligible,key=lambda i:digest(i['name']))[:count]


def load(path, root, evaluator, *, freeze=False):
    manifest=json.loads(path.read_text())
    if not freeze and manifest.get('format')!=1:
        raise ValueError('Freeze the case manifest before replaying it.')
    items=manifest.get('cases',[])
    if (not isinstance(items,list) or any(not isinstance(i,dict) or not isinstance(i.get('name'),str) or not i['name']
            or not isinstance(i.get('id',i['name']),str) or not i.get('id',i['name']) for i in items)):
        raise ValueError('Use named frozen cases with nonempty string ids.')
    if not 2<=len(items)<=16 or len({i.get('id',i['name']) for i in items})!=len(items):
        raise ValueError('Use 2–16 unique frozen cases; repeated functions need distinct ids.')
    cases=[]
    frozen=[]
    for item in items:
        def read(key, item=item):
            file=Path(item[key])
            return (file if file.is_absolute() else path.parent/file).read_text()
        source,header=read('before'),read('header')
        hashes=dict(source_sha256=digest(source),header_sha256=digest(header))
        if not freeze and any(item.get(k)!=v for k,v in hashes.items()):
            raise ValueError('A frozen source or header changed; freeze a separate comparison set.')
        evaluate,scope,_,_,_=evaluator(root,item['name'],header)
        control_hash={}
        if item.get('control'):
            control=read('control');control_hash={'control_sha256':digest(control)}
            if not freeze and item.get('control_sha256')!=control_hash['control_sha256']:
                raise ValueError('A frozen exact control changed.')
            if not evaluate(control).get('matched'):
                raise ValueError('The supplied exact control no longer passes the copied judge.')
        if not freeze and item.get('scope')!=scope:
            raise ValueError('The frozen target, manifest or compiler changed.')
        case=dict(name=item['name'],source=source,header=header)
        if 'id' in item:case['id']=item['id']
        if type(item.get('expect_recovery',False)) is not bool:raise ValueError('Expected recovery must be boolean.')
        case['expect_recovery']=item.get('expect_recovery',False)
        cases.append(case)
        frozen.append({**item,**hashes,**control_hash,'scope':scope,
                       'before':str((path.parent/item['before']).resolve()),
                       'header':str((path.parent/item['header']).resolve())})
    for item in frozen:
        if item.get('control'):item['control']=str((path.parent/item['control']).resolve())
    return cases,dict(format=1,cases=frozen)
