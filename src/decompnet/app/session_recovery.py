"""Durable CLI conversation IDs and function handoffs for interrupted workers."""
import uuid


def checkpoint(net, session):
    rows = net.store.all('SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id '
                         'JOIN agents a ON a.id=c.agent_id JOIN projects p ON p.id=f.project_id '
                         'WHERE a.name=? AND p.slug=? ORDER BY f.id', session.agent_name, session.slug)
    if rows:
        session.options['resume_functions'] = [row['name'] for row in rows]


def conversation(session, value):
    try:
        value = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return False
    key = 'focus_conversation' if session.options.get('focus_id') else 'resume_conversation'
    session.options[key] = value
    return True


def completed(session, net=None):
    """An ordinary completed turn keeps the normal fresh-session policy."""
    if not session.options.get('focus_id'):
        session.options.pop('resume_conversation', None)
        session.options.pop('resume_functions', None)
        if net is not None:
            with net.store.tx() as db:
                db.execute('DELETE FROM settings WHERE key=?', ('helper-conversation:' + session.id,))


def handoff(session):
    names = session.options.get('resume_functions') or []
    if not names:
        return ''
    return ('\nRESTART RECOVERY: You were working on ' + ', '.join(names) +
            '. Continue the saved investigation. First read current project state and reacquire each function '
            'through the normal claim tool if it is still eligible. Respect other workers and already-saved matches. '
            'Your previous context may describe old claims or tool connections; use the current tools/settings.\n')


def api_messages(net, session, identity, messages=None):
    """Store only complete tool exchanges; endpoint identity prevents cross-model replay."""
    import json
    key = 'helper-conversation:' + session.id
    if messages is not None:
        value = json.dumps({'identity':identity, 'messages':messages})
        if len(value.encode()) <= 16 * 1024 * 1024:
            with net.store.tx() as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key,value))
        return None
    row = net.store.one('SELECT value FROM settings WHERE key=?', key)
    if not row:
        return None
    try:
        value = json.loads(row['value'])
        saved = value['messages']
        if value['identity'] == identity and isinstance(saved, list) and len(saved) >= 2:
            return saved
    except (ValueError, KeyError, TypeError):
        pass
    return None
