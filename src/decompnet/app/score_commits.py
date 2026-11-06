"""Batch distinct functions' improved best drafts into scoped local progress commits."""
import json
import re
import time
import uuid

from ..network import NetworkError
from ..project import compiler_for, function_compiler_key
from . import auto_push, match_commits, repository_git, repository_progress, repository_sync, saves

# Lower judge distance is better; exact matches have their own commit pipeline.
PENDING = """SELECT f.id,f.name,f.unit,f.vram,f.rom_offset,f.best_score,b.score AS baseline_score
 FROM functions f LEFT JOIN score_commit_baselines b ON b.function_id=f.id AND b.project_id=f.project_id
 WHERE f.project_id=? AND f.status='open' AND f.best_source IS NOT NULL AND f.best_source!=''
 AND f.best_score>=0 AND (b.score IS NULL OR f.best_score<b.score)"""


def status(net, proj):
    cfg = net.cfg(proj).get('score_commits', {})
    state = {key: cfg.get(key, default) for key, default in
             (('enabled', False), ('threshold', 10), ('error', ''), ('last_commit', ''))}
    state['pending'] = net.store.one('SELECT COUNT(*) AS n FROM ('+PENDING+')', proj['id'])['n'] if state['enabled'] else 0
    if state['enabled']:
        state['items'] = [{'name':row['name'], 'before':row['baseline_score'], 'score':row['best_score']}
                          for row in net.store.all(PENDING+' ORDER BY f.name LIMIT 20', proj['id'])] if state['pending'] else []
    return state


def configure(net, slug, enabled, threshold=10, folder=''):
    if not isinstance(enabled, bool):raise NetworkError('Choose score-improvement commits with a boolean value.')
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 1 <= threshold <= 10000:
        raise NetworkError('Choose a batch size from 1 to 10,000 improved functions.')
    proj = net.store.one('SELECT * FROM projects WHERE slug=?', slug)
    if proj is None:raise NetworkError('Open a project before configuring score-improvement commits.')
    settings = dict(enabled=enabled, threshold=threshold, token=uuid.uuid4().hex, error='')
    if not enabled:
        # Opting out must not wait for a build, source recheck or a queued push.
        with net.store.tx() as db:
            row = db.execute('SELECT config FROM projects WHERE id=?', (proj['id'],)).fetchone()
            cfg = json.loads(row['config']);cfg['score_commits'] = settings
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), proj['id']))
            db.execute('DELETE FROM score_commit_baselines WHERE project_id=?', (proj['id'],))
        return status(net, net.store.one('SELECT * FROM projects WHERE id=?', proj['id']))
    if not repository_sync._LOCK.acquire(blocking=False):
        raise NetworkError('Another repository operation is running. Wait for it to finish and retry.')
    try:
        with net.progress._project_lock(slug):
            previous = net.cfg(proj).get('score_commits', {})
            if enabled:
                root, branch = match_commits.checkout_settings(net, slug, folder)
                settings.update(folder=str(root), branch=branch)
                if previous.get('enabled') and any(previous.get(key) != settings[key] for key in ('folder', 'branch')):
                    raise NetworkError('The progress checkout or branch changed. Disable and re-enable score-improvement commits.')
            # Baselines and preference change together; merely changing the threshold keeps pending improvements.
            with net.store.tx() as db:
                row = db.execute('SELECT config FROM projects WHERE id=?', (proj['id'],)).fetchone()
                cfg = json.loads(row['config'])
                if not enabled or not previous.get('enabled'):
                    db.execute('DELETE FROM score_commit_baselines WHERE project_id=?', (proj['id'],))
                    if enabled:
                        db.execute('INSERT INTO score_commit_baselines(project_id,function_id,score) SELECT project_id,id,best_score FROM functions WHERE project_id=?', (proj['id'],))
                cfg['score_commits'] = settings
                db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), proj['id']))
        return status(net, net.store.one('SELECT * FROM projects WHERE id=?', proj['id']))
    finally:
        repository_sync._LOCK.release()


def _change(net, slug, token, **values):
    def apply(cfg):
        if cfg.get('score_commits', {}).get('token') == token:cfg['score_commits'].update(values)
    auto_push._change(net, slug, apply)


def _acknowledge(net, rows):
    with net.store.tx() as db:
        for row in rows:
            db.execute('INSERT INTO score_commit_baselines(project_id,function_id,score) '
                       'SELECT project_id,id,? FROM functions WHERE id=? '
                       'ON CONFLICT(project_id,function_id) DO UPDATE SET score=excluded.score', (row['best_score'], row['id']))


def scoped_snapshot(net, proj, root, rows, output):
    files, _, current = repository_progress.snapshot(net, proj['slug'], root, output, allow_unrelated_edits=True)
    prefix = saves.default_folder(net, proj['slug']).resolve().relative_to(root).as_posix()
    prefix = '' if prefix == '.' else prefix + '/'
    manifest_path = prefix+saves.MANIFEST
    head = repository_git.git(root, 'rev-parse', 'HEAD')
    base = json.loads(repository_git.git(root, 'show', head+':'+manifest_path))
    if match_commits.layout_shape(base) != match_commits.layout_shape(current) or base['game']['sha1'] != current['game']['sha1']:
        raise NetworkError('Commit a manual progress checkpoint after game or function-layout changes, then retry improvement commits.')
    # Synchronization may have judged contributions; select only still-improved open functions.
    wanted = {row['id'] for row in rows}
    rows = [row for row in net.store.all(PENDING, proj['id']) if row['id'] in wanted]
    chosen, needed = {}, []
    names = {row['name'] for row in rows}
    for row in rows:
        name, unit = row['name'], row['unit'] or ''
        if 'matched_by' in base['functions'].get(name, {}):
            raise NetworkError('A committed match no longer matches. Review and commit a manual checkpoint before batching its draft.')
        info = current['functions'].get(name, {})
        if info.get('best_score') != row['best_score']:
            raise NetworkError('A draft changed during capture. Wait for the next autosave and retry.')
        base['functions'][name] = info
        path = prefix+f'best/{name}.c'
        chosen[path] = files[path]
        header = current['headers'][unit]
        base.setdefault('headers', {})[unit] = header
        needed.append(prefix+'include/'+header)
        key = function_compiler_key(row) or 'function:'+name
        base.setdefault('function_compilers', {})[key] = {**compiler_for(current, row['vram'], row), 'function':name, 'vram':row['vram']}
    include = re.compile(rb'^\s*#\s*include\s*"([^"\n]+)"', re.M)
    for data in chosen.values():needed.extend(prefix+'include/'+item.decode() for item in include.findall(data))
    while needed:
        path = needed.pop()
        repository_sync.inside(root, path)
        if path in chosen:continue
        if path not in files:raise NetworkError('A saved draft header is missing. Save current progress and retry.')
        chosen[path] = files[path]
        needed.extend(prefix+'include/'+item.decode() for item in include.findall(files[path]))
    base['notes'] = [note for note in base.get('notes', []) if note.get('function') not in names] + [note for note in current.get('notes', []) if note.get('function') in names]
    base['saved_at'], base['revision'] = current['saved_at'], current.get('revision')
    chosen[manifest_path] = (json.dumps(base, indent=1)+'\n').encode()
    return chosen, rows, head


def process(net, slug, output=lambda message: None):
    proj = net.store.one('SELECT * FROM projects WHERE slug=?', slug)
    settings = net.cfg(proj).get('score_commits', {}) if proj else {}
    if not settings.get('enabled') or settings.get('retry_at', 0) > time.time():return
    rows = net.store.all(PENDING, proj['id'])
    if len(rows) < settings['threshold']:return
    if not repository_sync._LOCK.acquire(blocking=False):return
    try:
        with net.progress._project_lock(slug):
            root = repository_sync.checkout(settings['folder'], clean=False)
            if not repository_sync.progress_checkout(net, slug, root) or repository_git.git(root, 'symbolic-ref', '--quiet', 'HEAD') != settings['branch']:
                raise NetworkError('The progress checkout or branch changed. Disable and re-enable score-improvement commits.')
            prefix = saves.default_folder(net, slug).resolve().relative_to(root)
            base = json.loads(repository_git.git(root, 'show', 'HEAD:'+(prefix/saves.MANIFEST).as_posix()))
            current = json.loads((saves.default_folder(net, slug)/saves.MANIFEST).read_text(encoding='utf-8'))
            if match_commits.layout_shape(base) != match_commits.layout_shape(current) or base['game']['sha1'] != current['game']['sha1']:
                raise NetworkError('Commit a manual checkpoint after game or function-layout changes, then retry improvement commits.')
            # Recover a commit completed before acknowledgement, or a user's manual checkpoint.
            acknowledged = [row for row in rows if base.get('functions', {}).get(row['name'], {}).get('best_score', float('inf')) <= row['best_score']]
            _acknowledge(net, acknowledged)
            rows = [row for row in rows if row['id'] not in {item['id'] for item in acknowledged}]
            if len(rows) < settings['threshold']:return
            files, selected, head = scoped_snapshot(net, proj, root, rows, output)
            if len(selected) < settings['threshold']:return
            fresh = net.store.one('SELECT * FROM projects WHERE id=?', proj['id'])
            if net.cfg(fresh).get('score_commits', {}).get('token') != settings['token']:return
            details = '\n'.join(f"{row['name']}: distance {row['baseline_score'] if row['baseline_score'] is not None else 'unscored'} -> {row['best_score']}" for row in selected)
            commit = repository_progress.commit_snapshot(root, files, set(files), output, expected_head=head,
                expected_branch=settings['branch'], message=f'Improve drafts for {len(selected)} functions\n\n'+details)
            _acknowledge(net, selected)
            _change(net, slug, settings['token'], error='', last_commit=commit, retry_at=0)
            output(f'Committed improved drafts for {len(selected)} functions. Automatic push uses its own commit threshold.')
    except (NetworkError, OSError, ValueError, KeyError) as error:
        _change(net, slug, settings['token'], error=str(error) if isinstance(error, NetworkError) else
                'Could not capture improved drafts. Review saved progress and Git setup, then retry.', retry_at=time.time()+15)
    finally:
        repository_sync._LOCK.release()
