"""Private multi-case replay inputs, pinned to source, header, target and compiler evidence."""
import hashlib
import json
from pathlib import Path


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def load(path, root, evaluator, *, freeze=False):
    manifest=json.loads(path.read_text())
    if not freeze and manifest.get('format')!=1:
        raise ValueError('Freeze the case manifest before replaying it.')
    items=manifest.get('cases',[])
    if not 2<=len(items)<=16 or len({i['name'] for i in items})!=len(items):
        raise ValueError('Use 2–16 unique frozen functions.')
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
        _,scope,_,_,_=evaluator(root,item['name'],header)
        if not freeze and item.get('scope')!=scope:
            raise ValueError('The frozen target, manifest or compiler changed.')
        cases.append(dict(name=item['name'],source=source,header=header))
        frozen.append({**item,**hashes,'scope':scope,
                       'before':str((path.parent/item['before']).resolve()),
                       'header':str((path.parent/item['header']).resolve())})
    return cases,dict(format=1,cases=frozen)
