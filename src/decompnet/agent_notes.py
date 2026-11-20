"""Function handoffs belong to a helper session; score reductions are measured, never parsed from prose."""
import hashlib
import json
import re
import time
from pathlib import Path

from .project import compiler_for


def rom_inputs(read, proj, fn, version, compiler=None):
    cfg = json.loads(proj['config'])
    if not cfg.get('compiler'):
        return None
    profile = compiler if compiler is not None else compiler_for(cfg, fn['vram'], fn)
    header = read(
        "SELECT header FROM context_versions WHERE project_id=? AND version=? AND unit IN (?, '') "
        "ORDER BY (unit=?) DESC LIMIT 1",
        proj['id'], version, fn['unit'], fn['unit'],
    )
    if header is None:
        return None
    return _key(fn, (profile, cfg.get('gp'), proj['arch'], proj['big_endian']), header['header'])


def project_inputs(fp, unit, fn):
    from .candidate_scope import dependencies, digest

    # Candidate C changes on every try; hash its headers and build controls separately.
    source = fp.root / unit.source if unit.source else None
    headers = [(Path(path).relative_to(fp.root).as_posix(), value)
               for path, value in dependencies(fp.root, unit.source) if path != str(source)]
    controls = [(name, digest(fp.root / name)) for name in ('Makefile', 'build.ninja', 'objdiff.json', 'configure.py', '.decomp.wtf.json')]
    target = digest(unit.target) if unit.target else None
    return _key(fn, controls, headers, target)


def _key(fn, compiler, headers, project_target=None):
    target = fn['target']
    extent = (fn['unit'], fn['size'], fn['n_insns'], fn['vram'], fn['rom_offset'], fn['relocs'], fn['asm'],
              hashlib.sha256(target).hexdigest() if target is not None else None, project_target)
    return ':'.join(hashlib.sha256(json.dumps(part, sort_keys=True, default=str).encode()).hexdigest()
                    for part in (compiler, headers, extent))


def current_inputs(net, store, fn):
    proj = store.one('SELECT * FROM projects WHERE id=?', fn['project_id'])
    if json.loads(proj['config']).get('kind') == 'fork':
        from .forks.project import ForkError, ForkProject

        try:
            fp = ForkProject(Path(proj['root']), create_state=False)
            return project_inputs(fp, fp.unit(fn['unit']), fn)
        except (ForkError, OSError, ValueError):
            return None
    ctx = net._context(fn['project_id'], fn['unit'])
    if ctx is None:
        return None
    return rom_inputs(store.one, proj, fn, ctx['version'])


def valid_inputs(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}(?::[0-9a-f]{64}){0,2}', value) else None


def changed_inputs(current, tested):
    if not current or not tested or current == tested:
        return ''
    parts = ('compiler or build settings', 'headers', 'function target or boundaries')
    current, tested = current.split(':'), tested.split(':')
    if len(current) != len(parts) or len(tested) != len(parts):
        return 'build inputs or function boundaries'
    return ', '.join(label for label, now, then in zip(parts, current, tested, strict=True) if now != then)


def session_for(db, project, agent, at, *, agent_name=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='helper_sessions'").fetchone():
        return None
    join = ' JOIN agents a ON a.name=s.agent' if agent_name is None else ''
    identity = 'a.id' if agent_name is None else 's.agent'
    return db.execute('SELECT s.* FROM helper_sessions s JOIN projects p ON p.slug=s.project'+join+' '
                      'WHERE p.id=? AND '+identity+'=? AND s.started_at<=? '
                      'AND (s.ended_at IS NULL OR s.ended_at>=?) ORDER BY s.id DESC LIMIT 1',
                      (project, agent if agent_name is None else agent_name, at, at)).fetchone()


def scores(db, function, agent, session, inputs=None):
    start, end = session['started_at'], session['ended_at'] or time.time()
    scope = ' AND handoff_inputs=?' if inputs is not None else ''
    scope_args = (inputs,) if inputs is not None else ()
    first = db.execute('SELECT created_at,score_before,score FROM attempts WHERE function_id=? AND agent_id=? '
                       'AND score IS NOT NULL AND created_at BETWEEN ? AND ?' + scope
                       + ' ORDER BY created_at,id LIMIT 1', (function, agent, start, end, *scope_args)).fetchone()
    if first is None:
        return current_scores(db, function, start) if inputs is None else (None, None)
    before = db.execute('SELECT MIN(score) FROM attempts WHERE function_id=? AND created_at<?' + scope,
                        (function, first['created_at'], *scope_args)).fetchone()[0]
    after = db.execute('SELECT MIN(score) FROM attempts WHERE function_id=? AND agent_id=? '
                       'AND created_at BETWEEN ? AND ?' + scope,
                       (function, agent, start, end, *scope_args)).fetchone()[0]
    if before is None:
        before = first['score_before'] if inputs is None and first['score_before'] is not None else first['score']
    return before, min(before, after)


def current_scores(db, function, at):
    # A note without new compiles still records the measured saved score, including zero.
    score = db.execute('SELECT MIN(score) FROM attempts WHERE function_id=? AND created_at<=?',
                       (function, at)).fetchone()[0]
    return score, score


def note_scores(db, function, agent, at, inputs=None):
    previous = db.execute('SELECT MAX(created_at) FROM notes WHERE function_id=? AND agent_id=? AND created_at<?',
                          (function, agent, at)).fetchone()[0]
    measured = scores(db, function, agent, {'started_at': previous or 0, 'ended_at': at}, inputs)
    return current_scores(db, function, at) if inputs is None and measured == (None, None) else measured


def latest_inputs(db, function, agent, since, at):
    row = db.execute(
        'SELECT handoff_inputs FROM attempts WHERE function_id=? AND agent_id=? AND score IS NOT NULL '
        'AND created_at BETWEEN ? AND ? ORDER BY created_at DESC,id DESC LIMIT 1',
        (function, agent, since, at),
    ).fetchone()
    return row['handoff_inputs'] if row else None


def room_handoff(store, fn, agent, notes):
    """A room host may not have judged the guest's tries. Share advisory evidence from the current lease."""
    if not notes.strip():
        return None
    with store.lock:
        db = store.conn
        claim = db.execute('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?',
                           (fn['id'], agent.id)).fetchone()
        if claim is None:
            return None
        now = time.time()
        since = claim['claimed_at']
        inputs = latest_inputs(db, fn['id'], agent.id, since, now)
        if inputs is None:
            return None
        before, after = scores(db, fn['id'], agent.id, {'started_at': since, 'ended_at': now}, inputs)
        return [before, after, inputs]


def validate_room_handoff(value):
    if not isinstance(value, list) or len(value) != 3:
        return None
    before, after, inputs = value
    if (not valid_inputs(inputs) or type(after) is not int or not 0 <= after < 2**63
            or before is not None and (type(before) is not int or not 0 <= before < 2**63)):
        return None
    return before, after, inputs


def save(db, project, function, agent, text, at=None, measured=None, *, agent_name=None, room_evidence=None):
    # An unmatched function needs one copy of the finding even when another helper repeats it.
    if function and db.execute(
        "SELECT 1 FROM functions f JOIN notes n ON n.function_id=f.id AND n.project_id=f.project_id "
        "WHERE f.id=? AND f.project_id=? AND f.status<>'matched' AND n.text=?",
        (function, project, text),
    ).fetchone():
        return
    at = time.time() if at is None else at
    session = session_for(db, project, agent, at, agent_name=agent_name) if function else None
    inputs = latest_inputs(db, function, agent, session['started_at'] if session else 0, at) if function else None
    if room_evidence is not None:
        before, after, inputs = room_evidence
    elif measured is not None:
        before, after = measured
    elif session:
        before, after = scores(db, function, agent, session, inputs)
    else:
        before, after = note_scores(db, function, agent, at, inputs) if function else (None, None)
    previous = db.execute('SELECT id,text FROM notes WHERE function_id=? AND agent_id=? AND session_id=? '
                          'AND handoff_inputs IS ? ORDER BY id DESC LIMIT 1',
                          (function, agent, session['id'], inputs)).fetchone() if session else None
    if previous:
        combined = previous['text'] if text in previous['text'] else previous['text'] + '\n\n' + text
        db.execute('UPDATE notes SET text=?,score_before=?,score_after=? WHERE id=?',
                   (combined, before, after, previous['id']))
    else:
        db.execute('INSERT INTO notes(project_id,function_id,agent_id,text,created_at,session_id,score_before,score_after,handoff_inputs) '
                   'VALUES(?,?,?,?,?,?,?,?,?)', (project, function, agent, text, at,
                                              session['id'] if session else None, before, after, inputs))
    return True


def grouped(store, rows, project):
    result, groups = [], {}
    with store.lock:
        db = store.conn
        for raw in rows:
            row = dict(raw)
            session = session_for(db, project, row['agent_id'], row['created_at'], agent_name=row['agent'])
            key = (row['agent_id'], row.get('session_id') or (session['id'] if session else None))
            # Exact imported timestamps can identify a recording batch, but never imply a known session.
            if key[1] is None or row['agent'] == 'you':
                key = ('legacy', row['agent_id'], row['created_at']) if row['agent_id'] is not None and row['agent'] != 'you' else ('note', row['id'])
            inputs = row.get('handoff_inputs')
            key = (*key, inputs)
            row['ids'] = [row['id']]
            if row.get('score_after') is None and row['function_id'] and row['agent_id']:
                row['score_before'], row['score_after'] = (scores(db, row['function_id'], row['agent_id'], session, inputs)
                    if session else note_scores(db, row['function_id'], row['agent_id'], row['created_at'], inputs))
            if key in groups:
                target = groups[key]
                target['ids'].append(row['id'])
                if row['text'] not in target['text']:
                    target['text'] = row['text'] + '\n\n' + target['text']
            else:
                groups[key] = row
                result.append(row)
    return result


def finish(db, session_id):
    session = db.execute('SELECT * FROM helper_sessions WHERE id=?', (session_id,)).fetchone()
    for note in db.execute('SELECT id,function_id,agent_id,handoff_inputs FROM notes WHERE session_id=?', (session_id,)).fetchall():
        before, after = scores(db, note['function_id'], note['agent_id'], session, note['handoff_inputs'])
        db.execute('UPDATE notes SET score_before=?,score_after=? WHERE id=?', (before, after, note['id']))
