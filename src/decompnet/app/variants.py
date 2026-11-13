"""Visible games above independent targets, with many-to-many logical function identities.

Membership never moves project rows or attempts. Shared revisions are immutable and published
only after every bound instance passes its own judge. PROJECT adapters are deliberately gated
until their object builds can supply the same verification and export contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager

from ..network import IN_BUILD, NetworkError


def member(net, slug):
    return net.store.one('SELECT * FROM game_variants WHERE project=?', slug)


def game(net, slug):
    own = member(net, slug)
    return net.store.one('SELECT * FROM variant_games WHERE id=?', own['game']) if own else None


def members(net, slug):
    own = member(net, slug)
    return net.store.all('SELECT * FROM game_variants WHERE game=? ORDER BY variant', own['game']) if own else []


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', value):
        raise NetworkError('Use a variant ID starting with a letter, followed by letters, numbers, - or _ (40 characters maximum).')
    return value


def _target(net, slug):
    proj = net.project_row(slug)
    if not net.is_rom(proj) or net.fork_slug(slug) or slug in net.links:
        raise NetworkError('Attach local ROM projects. PROJECT-mode and room adapters are not available yet.')
    return proj


def attach(net, slug, other, variant, primary='default'):
    """Attach a target without changing either target's source, claims, history or settings."""
    variant, primary = _id(variant), _id(primary)
    proj, donor = _target(net, slug), _target(net, other)
    if slug == other or net.cfg(proj).get('platform') != net.cfg(donor).get('platform'):
        raise NetworkError('Choose a different local ROM project of the same console.')
    with net.store.tx() as db:
        own = db.execute('SELECT * FROM game_variants WHERE project=?', (slug,)).fetchone()
        attached = db.execute('SELECT * FROM game_variants WHERE project=?', (other,)).fetchone()
        if attached:
            raise NetworkError('That project already belongs to a game. Detach it before attaching it here.')
        gid = own['game'] if own else uuid.uuid4().hex
        _metadata_unlocked(db, gid)
        if db.execute('SELECT 1 FROM game_variants WHERE game=? AND variant=?', (gid, variant)).fetchone() or (not own and variant == primary):
            raise NetworkError('That variant ID is already in use. Choose a different ID.')
        if not own:
            db.execute('INSERT INTO variant_games VALUES(?,?,?)', (gid, slug, proj['name'] or slug))
            db.execute('INSERT INTO game_variants VALUES(?,?,?,?)', (gid, primary, slug, primary))
        db.execute('INSERT INTO game_variants VALUES(?,?,?,?)', (gid, variant, other, variant))
    return status(net, slug)


def detach(net, slug, variant):
    """A detached target remains an ordinary project with all its work."""
    own = member(net, slug)
    if not own:
        raise NetworkError('This project has no attached variants.')
    with net.store.tx() as db:
        _metadata_unlocked(db, own['game'])
        if db.execute('SELECT 1 FROM variant_bindings WHERE game=? AND variant=?', (own['game'], variant)).fetchone():
            raise NetworkError('Remove this variant from its logical functions before detaching it.')
        db.execute('DELETE FROM game_variants WHERE game=? AND variant=?', (own['game'], variant))
        left = db.execute('SELECT project FROM game_variants WHERE game=? ORDER BY variant', (own['game'],)).fetchall()
        if not left:
            db.execute('DELETE FROM variant_games WHERE id=?', (own['game'],))
        else:
            db.execute('UPDATE variant_games SET primary_project=? WHERE id=? AND primary_project NOT IN '
                       '(SELECT project FROM game_variants WHERE game=?)', (left[0]['project'], own['game'], own['game']))
    return status(net, slug)


def visible(net, rows):
    """Collapse sidebar targets without opening any idle target's operational database."""
    groups = net.store.all('SELECT v.*,g.primary_project,g.title FROM game_variants v JOIN variant_games g ON g.id=v.game')
    by_project = {r['project']: r for r in groups}
    out = []
    for row in rows:
        own = by_project.get(row['slug'])
        if not own:
            out.append(row)
        elif own['project'] == own['primary_project']:
            group = [r for r in groups if r['game'] == own['game']]
            out.append(dict(row) | {'title': own['title'], 'variants': [dict(r) | {'summary': next((dict(item) for item in rows if item['slug'] == r['project']), {})} for r in group],
                                   'working': sum(r.get('working', 0) for r in rows if r['slug'] in {m['project'] for m in group})})
    return out


def status(net, slug):
    """Target-scoped progress is read only when the user explicitly opens Variants."""
    proj = net.project_row(slug)
    own, group = member(net, slug), game(net, slug)
    result = dict(supported=net.is_rom(proj) and not net.fork_slug(slug) and slug not in net.links,
                  game=dict(group) if group else None, selected=own['variant'] if own else None,
                  variants=[], logical=[])
    for item in members(net, slug):
        target = net.project_row(item['project'])
        counts = net.project_store(target['id']).all(
            f'SELECT status,COUNT(*) AS n FROM functions WHERE project_id=? AND {IN_BUILD} GROUP BY status', target['id'])
        result['variants'].append(dict(item) | {'functions': sum(r['n'] for r in counts),
                                                'matched': sum(r['n'] for r in counts if r['status'] == 'matched')})
    if own:
        for row in net.store.all('SELECT * FROM logical_functions WHERE game=? ORDER BY name,id', own['game']):
            bindings = net.store.all('SELECT * FROM variant_bindings WHERE logical=? ORDER BY variant,function_id', row['id'])
            instances = []
            target_slugs = {v['variant']: v['project'] for v in result['variants']}
            for binding in bindings:
                value = dict(binding)
                if value['function_id'] and value['variant'] in target_slugs:
                    target = net.project_row(target_slugs[value['variant']])
                    fn = net.project_store(target['id']).one('SELECT name FROM functions WHERE id=? AND project_id=?', value['function_id'], target['id'])
                    value['function'] = fn['name'] if fn else ''
                instances.append(value)
            latest = net.store.one('SELECT revision,source,header FROM variant_sources WHERE logical=? ORDER BY created_at DESC LIMIT 1', row['id'])
            result['logical'].append(dict(row) | {'instances': instances,
                                                 'shared': dict(latest) if latest else None})
    return result


def bind(net, slug, name, instances, logical=None):
    """Explicit many-to-many links: several functions per variant, or an absent/inlined instance.

    Function IDs are paired with a project slug, never a bare address (overlays can overlap).
    No matching verdict is inferred from identity or from regional similarity.
    """
    own = member(net, slug)
    if not own or not name or not isinstance(instances, list) or not instances:
        raise NetworkError('Attach variants and supply a name and at least one instance.')
    targets = {r['variant']: r['project'] for r in members(net, slug)}
    checked = []
    for item in instances:
        variant = item['variant']
        if variant not in targets or item.get('relation', 'implementation') not in {'implementation', 'split', 'merged', 'inlined', 'absent'}:
            raise NetworkError('Choose a member variant and a supported function relationship.')
        fid = int(item.get('function_id') or 0)
        fingerprint = ''
        if item.get('function'):
            proj = net.project_row(targets[variant])
            found = net.project_store(proj['id']).one('SELECT id FROM functions WHERE name=? AND project_id=?', item['function'], proj['id'])
            if found is None:raise NetworkError('Choose a function name in that variant.')
            fid = found['id']
        if fid:
            proj = net.project_row(targets[variant])
            function = net.project_store(proj['id']).one('SELECT target FROM functions WHERE id=? AND project_id=?', fid, proj['id'])
            if function is None:
                raise NetworkError('That function does not belong to the selected variant.')
            fingerprint = hashlib.sha256(bytes(function['target'] or b'')).hexdigest()
        elif item.get('relation') not in {'absent', 'inlined'}:
            raise NetworkError('An implementation needs a function ID; use absent or inlined for no standalone function.')
        defines = item.get('defines', {})
        if not isinstance(defines, dict) or any(not re.fullmatch(r'[A-Za-z_]\w*', k) or not re.fullmatch(r'[A-Za-z0-9_+ -]+', str(v)) for k, v in defines.items()):
            raise NetworkError('Defines need C identifiers and simple constant values.')
        checked.append((variant, fid, item.get('relation', 'implementation'), json.dumps(defines, sort_keys=True), item.get('override') or '', fingerprint))
    if len({(r[0], r[1]) for r in checked}) != len(checked):
        raise NetworkError('List each function once per logical identity and variant.')
    with net.store.tx() as db:
        _metadata_unlocked(db, own['game'])
        if logical and not db.execute('SELECT id FROM logical_functions WHERE id=? AND game=?', (logical, own['game'])).fetchone():
            raise NetworkError('Choose a logical function in this game.')
        logical = logical or uuid.uuid4().hex
        db.execute('INSERT INTO logical_functions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name', (logical, own['game'], name))
        db.execute('DELETE FROM variant_bindings WHERE logical=?', (logical,))
        db.executemany('INSERT INTO variant_bindings VALUES(?,?,?,?,?,?,?,?)',
                       [(own['game'], logical, *r) for r in checked])
    return logical


def _metadata_unlocked(db, gid):
    if db.execute('SELECT 1 FROM variant_edit_locks WHERE game=?', (gid,)).fetchone():
        raise NetworkError('Shared source is being verified or exported. Wait before changing variant membership.')


def _text(source, header, binding):
    defines = json.loads(binding['defines'])
    return ''.join(f'#define {k} {v}\n' for k, v in defines.items()) + header + '\n' + (binding['override_source'] or source)


@contextmanager
def edit_lock(net, gid, owner):
    """A database lease excludes conflicting shared edits across windows and helper processes."""
    token = uuid.uuid4().hex
    with net.store.tx() as db:
        # Never expire a live compiler's lease; recover only a process known to have exited.
        held = db.execute('SELECT process_id FROM variant_edit_locks WHERE game=?', (gid,)).fetchone()
        if held:
            try:os.kill(held['process_id'], 0)
            except ProcessLookupError:db.execute('DELETE FROM variant_edit_locks WHERE game=?', (gid,))
            else:raise NetworkError('Another shared-source edit is being verified. Wait for it to finish.')
        db.execute('INSERT INTO variant_edit_locks VALUES(?,?,?,?)', (gid, token, owner, os.getpid()))
    try:
        yield
    finally:
        with net.store.tx() as db:
            db.execute('DELETE FROM variant_edit_locks WHERE game=? AND token=?', (gid, token))


def publish(net, slug, logical, source, header='', expected=None, owner='user', log=print):
    """Publish a revision only if every implementation independently links exactly.

    Target source and headers stay untouched: even a cancelled verification cannot destroy an
    existing match. Export rechecks against current target inputs, so stale proofs confer no match.
    """
    own = member(net, slug)
    if not own or not net.store.one('SELECT 1 FROM logical_functions WHERE id=? AND game=?', logical, own['game']):
        raise NetworkError('Choose a logical function belonging to this game.')
    with edit_lock(net, own['game'], owner):
        previous = net.store.one('SELECT revision FROM variant_sources WHERE logical=? ORDER BY created_at DESC LIMIT 1', logical)
        if (previous['revision'] if previous else None) != expected:
            raise NetworkError('Shared source changed. Reload its current revision before editing.')
        bindings = net.store.all('SELECT b.*,v.project FROM variant_bindings b JOIN game_variants v '
                                'ON b.game=v.game AND b.variant=v.variant WHERE b.logical=? AND b.function_id<>0', logical)
        if not source.strip() or not bindings:
            raise NetworkError('Supply shared C and at least one standalone function to verify.')
        proofs = []
        for index, binding in enumerate(bindings):
            if hasattr(log, 'check_cancel'):log.check_cancel()
            if hasattr(log, 'progress'):log.progress({'done': index, 'total': len(bindings), 'phase': 'Verifying shared source'})
            proj = _target(net, binding['project'])
            fn = net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=? AND project_id=?', binding['function_id'], proj['id'])
            if fn is None or hashlib.sha256(bytes(fn['target'] or b'')).hexdigest() != binding['target_hash']:
                raise NetworkError('A linked function was replaced or removed. Update its logical identity before verifying source.')
            context = net._context(proj['id'], fn['unit'])['header']
            text = _text(source, header, binding)
            compiled = net._compile(proj, context, text, fn)
            if not compiled.ok:
                raise NetworkError(f"{binding['variant']}: {fn['name']} did not compile. {compiled.log[-1000:]}")
            _, exact, problems = net._evaluate(fn, proj, compiled.obj)
            if exact is not True:
                raise NetworkError(f"{binding['variant']}: {fn['name']} did not match exactly. " + '; '.join(problems))
            proofs.append({'variant': binding['variant'], 'function_id': fn['id'], 'project': binding['project'], 'target': hashlib.sha256(bytes(fn['target'])).hexdigest()})
            log(f"{binding['variant']}: {fn['name']} independently verified")
        if hasattr(log, 'check_cancel'):log.check_cancel()
        revision = uuid.uuid4().hex
        with net.store.tx() as db:
            db.execute('INSERT INTO variant_sources VALUES(?,?,?,?,?,?,?)',
                       (logical, revision, source, header, json.dumps(proofs), owner, time.time()))
        if hasattr(log, 'progress'):log.progress({'done': len(bindings), 'total': len(bindings), 'phase': 'Shared revision saved'})
        return {'logical': logical, 'revision': revision, 'verified': len(proofs)}


def shared_sources(net, slug):
    """Candidates for this target; the export's normal independent judge is still mandatory."""
    own = member(net, slug)
    if not own:return {}
    result = {}
    for binding in net.store.all('SELECT * FROM variant_bindings WHERE game=? AND variant=? AND function_id<>0', own['game'], own['variant']):
        revision = net.store.one('SELECT * FROM variant_sources WHERE logical=? ORDER BY created_at DESC LIMIT 1', binding['logical'])
        if revision:
            fid = binding['function_id']
            proj = net.project_row(slug)
            fn = net.project_store(proj['id']).one('SELECT target FROM functions WHERE id=? AND project_id=?', fid, proj['id'])
            if fn is None or hashlib.sha256(bytes(fn['target'] or b'')).hexdigest() != binding['target_hash']:
                raise NetworkError('A shared implementation was replaced or removed. Update its logical identity before export.')
            if fid in result:
                raise NetworkError('Several shared logical functions supply the same implementation. Choose one source before export.')
            result[fid] = {'source': _text(revision['source'], revision['header'], binding),
                           'logical': binding['logical'], 'revision': revision['revision'],
                           'shared_body': revision['header'] + '\n' + revision['source'],
                           'prefix': ''.join(f'#define {k} {v}\n' for k, v in json.loads(binding['defines']).items()),
                           'override': bool(binding['override_source'])}
    return result


def start_job(net, jobs, slug, kind, work):
    """Persist progress and terminal results; a new window follows the server-owned job ID."""
    own = member(net, slug)
    if not own:raise NetworkError('Attach a variant before starting shared work.')
    with jobs._lock:
        active = next((j for j in jobs.jobs.values() if j['kind'] == kind and j['state'] == 'running'
                       and member(net, j.get('activity_project') or '') and
                       member(net, j['activity_project'])['game'] == own['game']), None)
        if active:return active['id']
        durable = uuid.uuid4().hex
        with net.store.tx() as db:
            db.execute('INSERT INTO variant_jobs VALUES(?,?,?,?,?,?,?,?,?)',
                       (durable, own['game'], slug, 'running', '{}', None, None, time.time(), None))
        def run(log):
            def progress(value):
                log.progress(value)
                with net.store.tx() as db:
                    db.execute('UPDATE variant_jobs SET progress=? WHERE id=?', (json.dumps(value), durable))
            def output(line):log(line)
            output.progress, output.check_cancel, output.cancel_event = progress, log.check_cancel, log.cancel_event
            try:
                result = work(output)
                log.check_cancel()
                with net.store.tx() as db:
                    db.execute('UPDATE variant_jobs SET state=?,result=? WHERE id=?', ('done', json.dumps(result), durable))
                return result
            except BaseException as error:  # persist terminal state even on cancellation; the job runner reports it
                with net.store.tx() as db:
                    db.execute('UPDATE variant_jobs SET state=?,error=? WHERE id=?',
                               ('cancelled' if log.cancel_event.is_set() else 'error', str(error), durable))
                raise
        jid = jobs.start(kind, run, project=slug, cancel_message='Stopped. Previously verified shared revisions are retained.')
        with net.store.tx() as db:
            db.execute('UPDATE variant_jobs SET runtime_id=? WHERE id=?', (jid, durable))
        return jid


def job_status(net, jobs, slug):
    own = member(net, slug)
    if not own:return []
    rows = net.store.all('SELECT * FROM variant_jobs WHERE game=? ORDER BY created_at DESC LIMIT 20', own['game'])
    result = []
    for row in rows:
        value = dict(row)
        if value['state'] == 'running' and value['runtime_id'] not in jobs.jobs:
            value['state'] = 'interrupted'
            value['error'] = 'The app stopped during this job. Start it again to verify every affected variant.'
        value['progress'] = json.loads(value['progress'])
        value['result'] = json.loads(value['result']) if value['result'] else None
        result.append(value)
    return result
