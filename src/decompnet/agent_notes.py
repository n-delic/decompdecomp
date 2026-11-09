"""Function handoffs belong to a helper session; score reductions are measured, never parsed from prose."""
import time


def session_for(db, project, agent, at, *, agent_name=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='helper_sessions'").fetchone():
        return None
    join = ' JOIN agents a ON a.name=s.agent' if agent_name is None else ''
    identity = 'a.id' if agent_name is None else 's.agent'
    return db.execute('SELECT s.* FROM helper_sessions s JOIN projects p ON p.slug=s.project'+join+' '
                      'WHERE p.id=? AND '+identity+'=? AND s.started_at<=? '
                      'AND (s.ended_at IS NULL OR s.ended_at>=?) ORDER BY s.id DESC LIMIT 1',
                      (project, agent if agent_name is None else agent_name, at, at)).fetchone()


def scores(db, function, agent, session):
    first = db.execute('SELECT MIN(created_at) FROM attempts WHERE function_id=? AND agent_id=? '
                       'AND score IS NOT NULL AND created_at>=? AND created_at<=?',
                       (function, agent, session['started_at'], session['ended_at'] or time.time())).fetchone()[0]
    if first is None:
        return current_scores(db, function, session["started_at"])
    before = db.execute('SELECT MIN(score) FROM attempts WHERE function_id=? AND created_at<?',
                        (function, first)).fetchone()[0]
    after = db.execute('SELECT MIN(score) FROM attempts WHERE function_id=? AND agent_id=? '
                       'AND created_at>=? AND created_at<=?',
                       (function, agent, session['started_at'], session['ended_at'] or time.time())).fetchone()[0]
    if before is None:
        row = db.execute('SELECT score_before,score FROM attempts WHERE function_id=? AND agent_id=? AND created_at=? ORDER BY id LIMIT 1',
                         (function, agent, first)).fetchone()
        before = row['score_before'] if row['score_before'] is not None else row['score']
    return before, min(before, after) if before is not None and after is not None else after



def current_scores(db, function, at):
    # A note without new compiles still records the measured saved score, including zero.
    score = db.execute('SELECT MIN(score) FROM attempts WHERE function_id=? AND created_at<=?',
                       (function, at)).fetchone()[0]
    return score, score


def note_scores(db, function, agent, at):
    previous = db.execute('SELECT MAX(created_at) FROM notes WHERE function_id=? AND agent_id=? AND created_at<?',
                          (function, agent, at)).fetchone()[0]
    measured = scores(db, function, agent, {'started_at': previous or 0, 'ended_at': at})
    return current_scores(db, function, at) if measured == (None, None) else measured


def save(db, project, function, agent, text, at=None, measured=None, *, agent_name=None):
    at = time.time() if at is None else at
    session = session_for(db, project, agent, at, agent_name=agent_name) if function else None
    if measured is not None:
        before, after = measured
    elif session:
        before, after = scores(db, function, agent, session)
    else:
        before, after = note_scores(db, function, agent, at) if function else (None, None)
    previous = db.execute('SELECT id,text FROM notes WHERE function_id=? AND agent_id=? AND session_id=?',
                          (function, agent, session['id'])).fetchone() if session else None
    if previous:
        combined = previous['text'] if text in previous['text'] else previous['text'] + '\n\n' + text
        db.execute('UPDATE notes SET text=?,score_before=?,score_after=? WHERE id=?',
                   (combined, before, after, previous['id']))
    else:
        db.execute('INSERT INTO notes(project_id,function_id,agent_id,text,created_at,session_id,score_before,score_after) '
                   'VALUES(?,?,?,?,?,?,?,?)', (project, function, agent, text, at,
                                              session['id'] if session else None, before, after))


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
            row['ids'] = [row['id']]
            if row.get('score_before') is None and row['function_id'] and row['agent_id']:
                row['score_before'], row['score_after'] = (scores(db, row['function_id'], row['agent_id'], session)
                    if session else note_scores(db, row['function_id'], row['agent_id'], row['created_at']))
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
    for note in db.execute('SELECT id,function_id,agent_id FROM notes WHERE session_id=?', (session_id,)).fetchall():
        before, after = scores(db, note['function_id'], note['agent_id'], session)
        db.execute('UPDATE notes SET score_before=?,score_after=? WHERE id=?', (before, after, note['id']))
