"""Opt-in, local per-match Git commits, drained by the existing autosave worker after notes arrive."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid

from ..network import NetworkError
from ..project import function_compiler_key
from . import repository_git, repository_progress, repository_sync, saves

log = logging.getLogger('decompdecomp')


def layout_shape(manifest):
    return {(entry['name'], entry['vram'], entry['size']) for entry in manifest['layout']}


def status(net, proj):
    """Keep background failures visible without interrupting matching or opening a notification banner."""
    cfg = net.cfg(proj)
    enabled = bool(cfg.get('auto_commit_matches'))
    if not enabled:
        return {'enabled': False, 'pending': 0, 'error': '', 'last_commit': ''}
    pending = net.store.one("SELECT COUNT(*) n FROM auto_match_commits WHERE project_id=? AND state='pending'", proj['id'])['n']
    error = net.store.one("SELECT error FROM auto_match_commits WHERE project_id=? AND state='pending' AND error IS NOT NULL ORDER BY created_at DESC LIMIT 1", proj['id'])
    last = net.store.one("SELECT commit_sha FROM auto_match_commits WHERE project_id=? AND state='done' ORDER BY created_at DESC LIMIT 1", proj['id'])
    items = [dict(row) for row in net.store.all("SELECT f.name,q.error FROM auto_match_commits q JOIN functions f ON f.id=q.function_id WHERE q.project_id=? AND q.state='pending' ORDER BY q.created_at LIMIT 20", proj['id'])]
    return {'enabled': enabled, 'pending': pending, 'items': items, 'error': error['error'] if error else '',
            'last_commit': last['commit_sha'] if last else ''}


def configure(net, slug, enabled, folder=''):
    """Checking the box authorizes future local commits only; disabling cancels uncommitted work."""
    if not isinstance(enabled, bool):
        raise NetworkError('Choose automatic commits with a boolean value.')
    proj = net.project_row(slug)
    values = {'auto_commit_matches': enabled}
    if enabled:
        if not net.is_rom(proj):
            raise NetworkError('Automatic per-function commits need a linked portable progress checkout. Imported-build commit support is planned.')
        root = repository_sync.checkout(folder, clean=False)
        if not repository_sync.progress_checkout(net, slug, root):
            raise NetworkError('Choose this project’s linked progress checkout before enabling automatic commits.')
        branch = repository_git.git(root, 'symbolic-ref', '--quiet', 'HEAD',
                                     error='Switch to a named Git branch before enabling automatic commits.')
        for identity in ('GIT_AUTHOR_IDENT', 'GIT_COMMITTER_IDENT'):
            repository_git.git(root, 'var', identity, error='Set Git user.name and user.email in this checkout before enabling automatic commits.')
        if repository_git.git(root, 'diff', '--cached', '--name-only'):
            raise NetworkError('Review or commit staged changes before enabling automatic commits.')
        folder = saves.default_folder(net, slug).resolve()
        relative = folder.relative_to(root) / saves.MANIFEST
        base = json.loads(repository_git.git(root, 'show', 'HEAD:' + relative.as_posix()))
        current = json.loads((folder/saves.MANIFEST).read_text(encoding='utf-8'))
        if layout_shape(base) != layout_shape(current):
            raise NetworkError('Commit current progress once to save its function layout, then enable automatic match commits.')
        values.update(auto_commit_folder=str(root), auto_commit_branch=branch)
    net._update_cfg(slug, lambda cfg: cfg.update(values))
    if not enabled:
        with net.store.tx() as db:
            db.execute("UPDATE auto_match_commits SET state='cancelled',error=NULL WHERE project_id=? AND state='pending'", (proj['id'],))
    return status(net, net.store.one('SELECT * FROM projects WHERE id=?', proj['id']))


def enqueue(net, proj, function_id, *, restored=False):
    """Queue a transition into matched, after its normal exact-verdict record has committed."""
    try:
        if not net.cfg(proj).get('auto_commit_matches') or not net.is_rom(proj):
            return
        if restored and net.store.one("SELECT 1 FROM auto_match_commits WHERE function_id=? AND state='pending'", function_id):
            return
        row = net.store.one('SELECT f.name,f.vram,f.status,f.readable,f.matched_agent_id,v.attempt_id,v.details,v.source,v.header,a.created_at FROM functions f '
                            'JOIN verified_matches v ON v.function_id=f.id JOIN attempts a ON a.id=v.attempt_id WHERE f.id=?', function_id)
        if row is None or row['status'] != 'matched':
            return
        agent = net.store.one('SELECT name FROM agents WHERE id=?', row['matched_agent_id'])
        snapshot = {'details': json.loads(row['details']), 'source': row['source'], 'header': row['header'],
                    'name': row['name'], 'info': {'vram': row['vram'], 'matched_by': agent['name'] if agent else '', 'readable': bool(row['readable'])}}
        with net.store.tx() as db:
            fresh = db.execute('SELECT config FROM projects WHERE id=?', (proj['id'],)).fetchone()
            if fresh is None or not json.loads(fresh['config']).get('auto_commit_matches'):
                return
            db.execute('INSERT OR IGNORE INTO auto_match_commits(id,project_id,function_id,attempt_id,agent_id,created_at,notes_after,snapshot) VALUES(?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, proj['id'], function_id, row['attempt_id'], row['matched_agent_id'], row['created_at'], 0 if restored else row['created_at'], json.dumps(snapshot)))
    except Exception:
        # A Git convenience must never turn a successfully recorded match into an attempt error.
        log.exception('Could not queue an automatic match commit')


def scoped_snapshot(net, proj, root, job, output):
    """Advance only one function in the committed manifest, retaining its exact context and evidence."""
    head = repository_git.git(root, 'rev-parse', 'HEAD')
    files, _, current = repository_progress.snapshot(net, proj['slug'], root, output, allow_unrelated_edits=True)
    folder = saves.default_folder(net, proj['slug']).resolve()
    prefix = folder.relative_to(root).as_posix()
    prefix = '' if prefix == '.' else prefix + '/'
    manifest_path = prefix + saves.MANIFEST
    base = json.loads(repository_git.git(root, 'show', 'HEAD:' + manifest_path))
    fn = net.store.one('SELECT * FROM functions WHERE id=?', job['function_id'])
    if fn is None or fn['status'] != 'matched':
        raise NetworkError('The queued function no longer matches. Review it before retrying automatic commits.')
    name = fn['name']
    if layout_shape(base) != layout_shape(current) or base.get('game', {}).get('sha1') != current.get('game', {}).get('sha1'):
        raise NetworkError('Save a manual progress checkpoint after layout or game changes, then retry automatic commits.')
    captured = json.loads(job['snapshot'])
    bundle = captured['details']
    info = captured['info']
    if captured['name'] != name or bundle['vram'] != fn['vram'] or hashlib.sha256(fn['target'] or b'').hexdigest() != bundle['target_sha256']:
        raise NetworkError('The queued match target changed. Review the function before committing it.')
    if not bundle.get('header_sha256') or hashlib.sha256(captured['source'].encode()).hexdigest() != bundle['source_sha256'] or hashlib.sha256(captured['header'].encode()).hexdigest() != bundle['header_sha256']:
        raise NetworkError('The queued match needs complete saved verification evidence before it can be committed.')
    source = prefix + f'src/{name}.c'
    evidence = {'sources': {bundle['source_sha256']: captured['source']},
                'headers': {bundle['header_sha256']: captured['header']}}
    base['functions'][name] = info
    base['layout'] = [next(x for x in current['layout'] if x['name'] == name) if x['name'] == name else x for x in base['layout']]
    base['notes'] = [x for x in base.get('notes', []) if x.get('function') != name] + [x for x in current.get('notes', []) if x.get('function') == name]
    base['symbols'] = current.get('symbols', {})
    base['saved_at'] = current['saved_at']
    base['revision'] = current.get('revision', base.get('revision'))
    key = function_compiler_key(fn) or 'function:'+name
    profile = current.get('function_compilers', {}).get(key, {})
    base.setdefault('function_compilers', {})[key] = {**profile, **bundle['compiler'], 'function': name, 'vram': fn['vram']}
    proof = base.setdefault('verification', {})
    proof.setdefault('matches', {})[name] = bundle
    for kind, key in (('sources', 'source_sha256'), ('headers', 'header_sha256')):
        proof.setdefault(kind, {})[bundle[key]] = evidence[kind][bundle[key]]
    chosen = {source: saves._source_text(captured['source']).encode()}
    unit = bundle.get('unit') or ''
    header = saves._header_file(unit)
    base.setdefault('headers', {})[unit] = header
    chosen[prefix + 'include/' + header] = captured['header'].encode()
    needed = []
    # A function may include additional app-owned private headers. Copy their transitive includes only.
    include = re.compile(rb'^\s*#\s*include\s*"([^"\n]+)"', re.M)
    needed.extend(prefix + 'include/' + item.decode() for item in include.findall(chosen[source]))
    needed.extend(prefix + 'include/' + item.decode() for item in include.findall(captured['header'].encode()))
    while needed:
        path = needed.pop()
        repository_sync.inside(root, path)
        if path in chosen or path not in files:
            continue
        chosen[path] = files[path]
        needed.extend(prefix + 'include/' + item.decode() for item in include.findall(files[path]))
    chosen[manifest_path] = (json.dumps(base, indent=1)+'\n').encode()
    paths = set(chosen) | {prefix + f'best/{name}.c'}
    return chosen, paths, head


def process(net, slug, output=log.info):
    """Retry durable pending commits from the autosave worker; never push or block a compiler caller."""
    proj = net.store.one('SELECT * FROM projects WHERE slug=?', slug)
    if proj is None or not net.cfg(proj).get('auto_commit_matches'):
        return
    if not repository_sync._LOCK.acquire(blocking=False):
        return
    try:
        # Waiting-for-notes jobs must not starve ready matches beyond the batch limit.
        for job in net.store.all("SELECT q.* FROM auto_match_commits q WHERE q.project_id=? AND q.state='pending' AND q.retry_at<=? "
                                 "AND COALESCE(TRIM((SELECT n.text FROM notes n WHERE n.function_id=q.function_id "
                                 "AND n.agent_id=q.agent_id AND n.created_at>=q.notes_after ORDER BY n.id DESC LIMIT 1)), '')<>'' "
                                 "ORDER BY q.created_at LIMIT 20", proj['id'], time.time()):
            check = getattr(output, 'check_cancel', None)
            if check:check()
            proj = net.store.one('SELECT * FROM projects WHERE id=?', proj['id'])
            if proj is None:
                return
            cfg = net.cfg(proj)
            if not cfg.get('auto_commit_matches'):
                return
            try:
                note = net.store.one('SELECT text FROM notes WHERE function_id=? AND agent_id=? AND created_at>=? ORDER BY id DESC LIMIT 1',
                                     job['function_id'], job['agent_id'], job['notes_after'])
                if note is None or not note['text'].strip():
                    continue
                root = repository_sync.checkout(cfg['auto_commit_folder'], clean=False)
                if not repository_sync.progress_checkout(net, slug, root):
                    raise NetworkError('The linked progress checkout changed. Disable and re-enable automatic commits for the new folder.')
                branch = repository_git.git(root, 'symbolic-ref', '--quiet', 'HEAD')
                if repository_git.git(root, 'diff', '--cached', '--name-only'):
                    raise NetworkError('Review or commit staged changes before automatic match commits can continue.')
                if branch != cfg['auto_commit_branch']:
                    raise NetworkError('The Git branch changed. Disable and re-enable automatic commits on the intended branch.')
                marker = 'Decompdecomp-Match: ' + job['id']
                existing = repository_git.git(root, 'log', '--format=%H', '-1', '--fixed-strings', '--grep='+marker)
                if existing:
                    commit = existing
                else:
                    files, paths, head = scoped_snapshot(net, proj, root, job, output)
                    # Disabling while the snapshot was prepared cancels publication too.
                    fresh = net.store.one('SELECT * FROM projects WHERE id=?', proj['id'])
                    if fresh is None or not net.cfg(fresh).get('auto_commit_matches'):
                        return
                    commit = repository_progress.commit_snapshot(root, files, paths, output, expected_head=head,
                             expected_branch=branch, message=note['text'].strip()+'\n\n'+marker, allow_empty=True)
                with net.store.tx() as db:
                    db.execute("UPDATE auto_match_commits SET state='done',commit_sha=?,error=NULL WHERE id=?", (commit, job['id']))
            except (NetworkError, OSError, ValueError, KeyError) as error:
                with net.store.tx() as db:
                    db.execute('UPDATE auto_match_commits SET error=?,retry_at=? WHERE id=?', (str(error) if isinstance(error, NetworkError) else 'Could not read the progress checkpoint. Check its files and Git setup, then retry.', time.time()+15, job['id']))
    finally:
        repository_sync._LOCK.release()
