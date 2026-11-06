"""Function submissions attributed to recorded session models, never inferred from worker names."""
from .network import NetworkError


def tried(net, project, agent):
    tables = {r['name'] for r in net.store.all("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = net.store.all('SELECT * FROM helper_sessions WHERE project=? ORDER BY id DESC', project) if 'helper_sessions' in tables else []
    helper = net.store.one('SELECT model FROM helpers WHERE project=? AND name=?', project, agent.name) if 'helpers' in tables else None
    model = (helper['model'] if helper else '') or next((r['model'] for r in rows if r['agent'] == agent.name), '')
    model = model.split('#', 1)[0]
    if not model:
        raise NetworkError('Choose an explicit model before using the never-tried-by-model filter; this worker has no recorded model ID.')
    matching = [r for r in rows if r['model'].split('#', 1)[0] == model]
    # Sessions keep the model used at the time, even if a worker later switches models.
    attempts = net.store.all('SELECT t.function_id,t.created_at,a.name AS agent FROM attempts t '
                             'JOIN agents a ON a.id=t.agent_id JOIN functions f ON f.id=t.function_id '
                             'JOIN projects p ON p.id=f.project_id WHERE p.slug=?', project)
    by_agent = {}
    for row in matching:
        by_agent.setdefault(row['agent'], []).append((row['started_at'], row['ended_at']))
    return {a['function_id'] for a in attempts if any(
        a['created_at'] >= start and (end is None or a['created_at'] <= end)
        for start, end in by_agent.get(a['agent'], []))}
