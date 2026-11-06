"""Local SDK folders indexed once for every project; project overrides never change the global inventory."""

import hashlib
import json
import os
import re
from pathlib import Path

from ..network import NetworkError

KEY = 'sdk_library'
MAX_FILES = 512
MAX_ENTRIES = 30000


def inventory(net):
    row = net.store.one('SELECT value FROM settings WHERE key=?', KEY)
    return json.loads(row['value']) if row else {'folders': [], 'files': {}}


def allowed(net, proj, corpus):
    selection = net.cfg(proj).get('sdk_libraries')
    return selection is None or corpus in selection


def choose(net, slug, selection):
    if selection is not None:
        known = {r['corpus'] for r in net.store.all('SELECT DISTINCT corpus FROM sdk_signatures')}
        if not isinstance(selection, list) or any(not isinstance(c, str) or c not in known for c in selection):
            raise NetworkError('Choose libraries from the indexed local inventory, or use automatic detection.')
        selection = sorted(set(selection))
    with net.store.tx() as db:
        # Read under the write lock so a simultaneous project setting is not overwritten.
        row = db.execute('SELECT id,config FROM projects WHERE slug=?', (slug,)).fetchone()
        if row is None:
            raise NetworkError('Choose an existing project.')
        cfg = json.loads(row['config']); cfg['sdk_libraries'] = selection
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), row['id']))


def state(net, slug=None):
    saved = inventory(net)
    libraries = [dict(r) for r in net.store.all('SELECT corpus,COUNT(*) AS signatures FROM sdk_signatures GROUP BY corpus ORDER BY corpus')]
    selection, keys = None, set()
    if slug:
        proj = net.project_row(slug)
        selection = net.cfg(proj).get('sdk_libraries')
        keys = {p.key for p in net.twins.prints(proj).values() if p.size >= 32 and not p.asm}
    detected = {}
    if keys:
        console = net.console(proj)
        arch = proj['arch'] or console.arch
        big = proj['big_endian'] if proj['arch'] else console.big_endian
        for row in net.store.all('SELECT DISTINCT corpus,fingerprint FROM sdk_signatures WHERE arch=? AND big_endian=?',
                                 arch, big):
            if row['fingerprint'] in keys:
                detected.setdefault(row['corpus'], set()).add(row['fingerprint'])
    for row in libraries:
        row['detected'] = len(detected.get(row['corpus'], ()))
        row['enabled'] = selection is None or row['corpus'] in selection
    return {'folders': saved['folders'], 'libraries': libraries, 'selection': selection}


def scan(net, folders, log=lambda *_: None):
    if not isinstance(folders, list) or len(folders) > 16 or any(not isinstance(p, str) for p in folders):
        raise NetworkError('Choose up to 16 local SDK folders.')
    roots = sorted({Path(p).expanduser().resolve() for p in folders if p.strip()})
    if any(not p.is_dir() for p in roots):
        raise NetworkError('Every SDK folder must be an existing local directory.')
    paths, entries = set(), 0
    for root in roots:
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(directory)/d).is_symlink())
            entries += len(dirs) + len(files)
            if entries > MAX_ENTRIES:
                raise NetworkError('Choose smaller SDK folders (at most 30,000 directory entries).')
            for name in files:
                path = Path(directory)/name
                if path.suffix.lower() in ('.a', '.o') and not path.is_symlink():
                    paths.add(path)
                    if len(paths) > MAX_FILES:
                        raise NetworkError('Choose smaller SDK folders (at most 512 archives or object files).')
    old = inventory(net); records = {}; imported = 0
    for index, path in enumerate(sorted(paths)):
        log(f'Indexing SDK file {index + 1}/{len(paths)}: {path.name}')
        if path.stat().st_size > 100 * 1024 * 1024:
            raise NetworkError(f'SDK file exceeds 100 MB: {path.name}')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        prior = old['files'].get(str(path))
        if prior and prior['sha256'] == digest and prior.get('shape_index') == 1:
            records[str(path)] = prior
            continue
        version = re.search(r'2\.0[A-L]', str(path), re.I)
        family = 'libultra' if re.search(r'libg?ultra', path.name, re.I) else path.parent.name
        label = family + (' ' + version[0].upper() if version else '')
        corpus = f'{label} / {path.name} / sha256:{digest}'
        existed = net.store.one('SELECT 1 FROM sdk_signatures WHERE corpus=?', corpus) is not None
        count = net.twins.import_sdk_objects(path, corpus)
        owned = prior.get('owned', True) if prior and prior['corpus'] == corpus else not existed
        records[str(path)] = {'corpus': corpus, 'sha256': digest, 'signatures': count, 'owned': owned, 'shape_index': 1}
        imported += count
    retained = {r['corpus'] for r in records.values()}
    removed = {r['corpus'] for r in old['files'].values() if r.get('owned', True)} - retained
    with net.store.tx() as db:
        for corpus in removed:
            db.execute('DELETE FROM sdk_signatures WHERE corpus=?', (corpus,))
            db.execute('DELETE FROM sdk_sources WHERE corpus=?', (corpus,))
            db.execute('DELETE FROM sdk_shapes WHERE corpus=?', (corpus,))
        db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                   (KEY, json.dumps({'folders': [str(p) for p in roots], 'files': records})))
    from .sdk_names import name_projects
    name_projects(net)
    return {'files': len(records), 'signatures': imported, 'unchanged': sum(old['files'].get(p) == r for p, r in records.items())}
