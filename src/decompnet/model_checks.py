"""Function submissions attributed to recorded session models, never inferred from worker names."""
import json

from .network import NetworkError


def tried(net, project, agent):
    store = net.project_store(project)
    tables = {r['name'] for r in store.all("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = store.all('SELECT * FROM helper_sessions WHERE project=? ORDER BY id DESC', project) if 'helper_sessions' in tables else []
    helper = net.store.one('SELECT model FROM helpers WHERE project=? AND name=?', project, agent.name) if net.store.one("SELECT 1 FROM sqlite_master WHERE name='helpers'") else None
    model = (helper['model'] if helper else '') or next((r['model'] for r in rows if r['agent'] == agent.name), '')
    model = model.split('#', 1)[0]
    if not model:
        raise NetworkError('Choose an explicit model before using the never-tried-by-model filter; this worker has no recorded model ID.')
    matching = [r for r in rows if r['model'].split('#', 1)[0] == model]
    if not matching:
        return set()
    # Sessions keep the model used at the time, even if a worker later switches models. Start with those
    # windows so SQLite can use attempts(agent_id,created_at); do not transfer the project's entire attempt
    # history into Python on every selection check. DISTINCT also collapses retries and overlapping sessions.
    # An unended session has no upper bound; coalesce keeps closed windows indexable at both ends too.
    names = json.dumps(list({r['agent'] for r in matching}))
    identities = {r['name']:r['id'] for r in net.store.all(
        'SELECT id,name FROM agents WHERE name IN (SELECT value FROM json_each(?))', names)}
    windows = json.dumps([[identities[r['agent']], r['started_at'], r['ended_at']] for r in matching if r['agent'] in identities])
    attempts = store.all('''SELECT DISTINCT t.function_id FROM json_each(?) s
        JOIN attempts t ON t.agent_id=json_extract(s.value,'$[0]') AND t.created_at>=json_extract(s.value,'$[1]')
            AND t.created_at<=coalesce(json_extract(s.value,'$[2]'),?)
        JOIN functions f ON f.id=t.function_id JOIN projects p ON p.id=f.project_id WHERE p.slug=?''', windows, float('inf'), project)
    return {row['function_id'] for row in attempts}
