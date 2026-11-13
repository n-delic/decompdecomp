"""Opt-in local function commits with frozen verification evidence and author attribution."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid

from ..network import NetworkError
from ..project import function_compiler_key
from . import (
    match_commit_notes,
    repository_git,
    repository_operations,
    repository_progress,
    repository_setup,
    repository_sync,
    saves,
)

log = logging.getLogger('decompdecomp')


def layout_shape(manifest):
    return {(entry['name'], entry['vram'], entry['size']) for entry in manifest['layout']}


class LayoutChanged(NetworkError):
    """The last checkpoint's functions differ from the project's. `renamed` holds (old, new) pairs when every
    function kept its address and size, so only names moved: polishing or another release's names."""

    def __init__(self, message, renamed=()):
        super().__init__(message)
        self.renamed = list(renamed)


def layout_change(base, current, then):
    """Say what changed since the checkpoint, so the user knows why commits wait and what to do: polishing renamed
    a thousand functions of a finished game and its three queued matches waited with no reason given."""
    before = {(entry['vram'], entry['size']): entry['name'] for entry in base['layout']}
    after = {(entry['vram'], entry['size']): entry['name'] for entry in current['layout']}
    if before.keys() == after.keys():
        renamed = sorted((before[key], after[key]) for key in before if before[key] != after[key])
        shown = ', '.join(f'{old} → {new}' for old, new in renamed[:3]) + (f' and {len(renamed)-3:,} more' if len(renamed) > 3 else '')
        word = 'function was' if len(renamed) == 1 else 'functions were'
        return LayoutChanged(f'{len(renamed):,} {word} renamed since the last progress checkpoint ({shown}). '
                             f'Commit saved progress to save the new names, {then}.', renamed)
    moved = sorted({after[key] for key in after.keys() - before.keys()} | {before[key] for key in before.keys() - after.keys()})
    shown = ', '.join(moved[:3]) + (f' and {len(moved)-3:,} more' if len(moved) > 3 else '')
    return LayoutChanged(f'Functions were split, joined or resized since the last progress checkpoint ({shown}). '
                         f'Commit saved progress to save the new layout, {then}.')


def status(net, proj):
    """Keep background failures visible without interrupting matching or opening a notification banner."""
    store = net.project_store(proj['id'])
    cfg = net.cfg(proj)
    enabled = bool(cfg.get('auto_commit_matches'))
    polished = bool(cfg.get('auto_commit_polished'))
    if not enabled and not polished:
        return {'enabled': False, 'polished_enabled': False, 'pending': 0, 'error': '', 'last_commit': ''}
    pending = store.one("SELECT COUNT(*) n FROM auto_match_commits WHERE project_id=? AND state='pending'", proj['id'])['n']
    error = store.one("SELECT error FROM auto_match_commits WHERE project_id=? AND state='pending' AND error IS NOT NULL ORDER BY created_at DESC LIMIT 1", proj['id'])
    last = store.one("SELECT commit_sha FROM auto_match_commits WHERE project_id=? AND state='done' ORDER BY created_at DESC LIMIT 1", proj['id'])
    items = [dict(row) for row in store.all("SELECT f.name,q.error,q.agent_id,t.via AS method FROM auto_match_commits q JOIN functions f ON f.id=q.function_id LEFT JOIN attempts t ON t.id=q.attempt_id WHERE q.project_id=? AND q.state='pending' ORDER BY q.created_at LIMIT 20", proj['id'])]
    names = {row['id']: row['name'] for row in net.store.all(
        'SELECT id,name FROM agents WHERE id IN (SELECT value FROM json_each(?))',
        json.dumps(sorted({item['agent_id'] for item in items if item['agent_id'] is not None})))}
    for item in items:
        item['agent'] = names.get(item.pop('agent_id'))
    return {'enabled': enabled, 'polished_enabled': polished, 'pending': pending, 'items': items, 'error': error['error'] if error else '',
            'last_commit': last['commit_sha'] if last else ''}


def checkout_settings(net, slug, folder):
    """Shared preflight for opt-in local progress commits."""
    proj = net.project_row(slug)
    if not net.is_rom(proj):
        raise NetworkError('Automatic per-function commits need a linked portable progress checkout. Imported-build commit support is planned.')
    root = repository_sync.checkout(folder, clean=False)
    if not repository_progress.linked_checkout(net, slug, root):
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
        raise layout_change(base, current, 'then enable automatic commits')
    return root, branch


def configure(net, slug, enabled, folder='', kind='match'):
    """Checking the box authorizes future local commits only; disabling cancels uncommitted work."""
    store = net.project_store(slug)
    if not isinstance(enabled, bool):
        raise NetworkError('Choose automatic commits with a boolean value.')
    proj = net.project_row(slug)
    if kind not in ('match', 'polished'):raise NetworkError('Choose match or polished commits.')
    values = {('auto_commit_polished' if kind == 'polished' else 'auto_commit_matches'): enabled}
    if enabled:
        root, branch = checkout_settings(net, slug, folder)
        values.update(auto_commit_folder=str(root), auto_commit_branch=branch)
    net._update_cfg(slug, lambda cfg: cfg.update(values))
    if not enabled:
        pending = store.all("SELECT id,snapshot FROM auto_match_commits WHERE project_id=? AND state='pending'", proj['id'])
        ids = [row['id'] for row in pending if json.loads(row['snapshot']).get('kind', 'match') == kind]
        with store.tx() as db:
            db.executemany("UPDATE auto_match_commits SET state='cancelled',error=NULL WHERE id=? AND state='pending'",
                           [(identity,) for identity in ids])
    return status(net, store.one('SELECT * FROM projects WHERE id=?', proj['id']))


def enqueue(net, proj, function_id, *, restored=False, polished=False):
    """Queue a transition into matched, after its normal exact-verdict record has committed."""
    store = net.project_store(proj['id'])
    try:
        setting = 'auto_commit_polished' if polished else 'auto_commit_matches'
        if not net.cfg(proj).get(setting) or not net.is_rom(proj):
            return
        if restored and store.one("SELECT 1 FROM auto_match_commits WHERE function_id=? AND state='pending'", function_id):
            return
        row = store.one('SELECT f.name,f.vram,f.status,f.readable,f.matched_agent_id,v.attempt_id,v.details,v.source,v.header,a.created_at,a.agent_id AS proof_agent_id FROM functions f '
                            'JOIN verified_matches v ON v.function_id=f.id JOIN attempts a ON a.id=v.attempt_id WHERE f.id=?', function_id)
        if row is None or row['status'] != 'matched':
            return
        aid = row['proof_agent_id'] if polished else row['matched_agent_id']
        agent = net.store.one('SELECT name FROM agents WHERE id=?', aid)
        credited = net.store.one('SELECT name FROM agents WHERE id=?', row['matched_agent_id']) if polished else agent
        snapshot = {'details': json.loads(row['details']), 'source': row['source'], 'header': row['header'],
                    'name': row['name'], 'info': {'vram': row['vram'], 'matched_by': credited['name'] if credited else '', 'readable': bool(row['readable'])}}
        snapshot['kind'] = 'polished' if polished else 'match'
        snapshot['origin'] = match_commit_notes.origin(net, row['attempt_id'], snapshot['info']['matched_by'], restored=restored, project_id=proj['id'])
        if polished:snapshot['origin'].update(agent=agent['name'] if agent else 'unknown', method='polishing')
        with store.tx() as db:
            fresh = db.execute('SELECT config FROM projects WHERE id=?', (proj['id'],)).fetchone()
            if fresh is None or not json.loads(fresh['config']).get(setting):
                return
            db.execute('INSERT OR IGNORE INTO auto_match_commits(id,project_id,function_id,attempt_id,agent_id,created_at,notes_after,snapshot) VALUES(?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, proj['id'], function_id, row['attempt_id'], aid, row['created_at'], 0 if restored else row['created_at'], json.dumps(snapshot)))
    except Exception:
        # A Git convenience must never turn a successfully recorded match into an attempt error.
        log.exception('Could not queue an automatic match commit')


def scoped_snapshot(net, proj, root, job, output):
    """Advance only one function in the committed manifest, retaining its exact context and evidence."""
    store = net.project_store(proj['id'])
    # A reopened/deleted function cannot be committed. Avoid repeating a full
    # saved-progress synchronization on every retry of an already stale job.
    queued = store.one('SELECT status FROM functions WHERE id=?', job['function_id'])
    if queued is None or queued['status'] != 'matched':
        raise NetworkError('The queued function no longer matches. Review it before retrying automatic commits.')
    head = repository_git.git(root, 'rev-parse', 'HEAD')
    files, _, current = repository_progress.snapshot(net, proj['slug'], root, output, allow_unrelated_edits=True)
    folder = saves.default_folder(net, proj['slug']).resolve()
    prefix = folder.relative_to(root).as_posix()
    prefix = '' if prefix == '.' else prefix + '/'
    manifest_path = prefix + saves.MANIFEST
    base = json.loads(repository_git.git(root, 'show', 'HEAD:' + manifest_path))
    fn = store.one('SELECT * FROM functions WHERE id=?', job['function_id'])
    if fn is None or fn['status'] != 'matched':
        raise NetworkError('The queued function no longer matches. Review it before retrying automatic commits.')
    name = fn['name']
    if base.get('game', {}).get('sha1') != current.get('game', {}).get('sha1'):
        raise NetworkError('The last progress checkpoint is of another game. Commit saved progress once, and queued commits continue.')
    if layout_shape(base) != layout_shape(current):
        raise layout_change(base, current, 'and queued commits continue')
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
    store = net.project_store(slug)
    proj = store.one('SELECT * FROM projects WHERE slug=?', slug)
    if proj is None or not any(net.cfg(proj).get(k) for k in ('auto_commit_matches', 'auto_commit_polished')):
        return
    for job in store.all("SELECT * FROM auto_match_commits WHERE project_id=? AND state='pending' AND retry_at<=? ORDER BY created_at LIMIT 20", proj['id'], time.time()):
        check = getattr(output, 'check_cancel', None)
        if check:check()
        proj = store.one('SELECT * FROM projects WHERE id=?', proj['id'])
        if proj is None:return
        cfg = net.cfg(proj)
        kind = json.loads(job['snapshot']).get('kind', 'match')
        setting = 'auto_commit_polished' if kind == 'polished' else 'auto_commit_matches'
        if not cfg.get(setting):continue
        project_lock = net.progress._project_lock(slug)
        if not project_lock.acquire(blocking=False):return
        try:
            root = repository_sync.checkout(cfg['auto_commit_folder'], clean=False)
            with repository_operations.operation(root, slug, 'Committing a polished function' if kind == 'polished' else 'Committing a verified match', output) as log:
                if log is None:return
                # Another autosave worker may have completed this job while we read the queue.
                pending = store.one("SELECT 1 FROM auto_match_commits WHERE id=? AND state='pending'", job['id'])
                if not pending:continue
                note = store.one('SELECT text FROM notes WHERE function_id=? AND agent_id=? AND created_at>=? ORDER BY id DESC LIMIT 1',
                                     job['function_id'], job['agent_id'], job['notes_after'])
                message = match_commit_notes.message(net, job, note)
                if not repository_progress.linked_checkout(net, slug, root):
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
                    current = store.one('SELECT status FROM functions WHERE id=? AND project_id=?', job['function_id'], proj['id'])
                    if current is None or current['status'] != 'matched':
                        # A later recheck revoked the verdict; its frozen source must not be published.
                        with store.tx() as db:
                            db.execute("UPDATE auto_match_commits SET state='cancelled',error=NULL WHERE id=? AND state='pending'", (job['id'],))
                        log('Discarded an obsolete automatic match commit after its verdict changed.')
                        continue
                    log('Preparing saved files for '+json.loads(job['snapshot'])['name']+'…')
                    try:
                        files, paths, head = scoped_snapshot(net, proj, root, job, log)
                    except LayoutChanged as changed:
                        if not changed.renamed:
                            raise
                        # Only names moved, through checked renames: every match still verifies, so the commits
                        # save the checkpoint they need themselves, then commit this function on top of it.
                        count = len(changed.renamed)
                        log(f'Saving a progress checkpoint with {count:,} renamed functions…')
                        repository_setup.checkpoint(net, slug, root, saves.default_folder(net, slug).resolve(), log,
                                                    message=f'Save {count:,} renamed function{"s" * (count != 1)}')
                        files, paths, head = scoped_snapshot(net, proj, root, job, log)
                    # Disabling or moving the checkout cancels publication too.
                    fresh = store.one('SELECT * FROM projects WHERE id=?', proj['id'])
                    fresh_cfg = net.cfg(fresh) if fresh else {}
                    if not fresh_cfg.get(setting) or any(fresh_cfg.get(key) != cfg.get(key)
                            for key in ('auto_commit_folder', 'auto_commit_branch')):
                        return
                    commit = repository_progress.commit_snapshot(root, files, paths, log, expected_head=head,
                             expected_branch=branch, message=message, allow_empty=True)
                with store.tx() as db:
                    db.execute("UPDATE auto_match_commits SET state='done',commit_sha=?,error=NULL WHERE id=?", (commit, job['id']))
        except (NetworkError, OSError, ValueError, KeyError) as error:
            with store.tx() as db:
                db.execute('UPDATE auto_match_commits SET error=?,retry_at=? WHERE id=?', (str(error) if isinstance(error, NetworkError) else 'Could not read the progress checkpoint. Check its files and Git setup, then retry.', time.time()+15, job['id']))
        finally:
            project_lock.release()
