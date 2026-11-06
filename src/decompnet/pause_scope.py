"""Recorded session model identity for exhaustion; worker names are never model evidence."""
import hashlib


def recorded_model(net, proj, agent):
    if agent is None:return ''
    store = net.project_store(proj['id'])
    name=agent.name if hasattr(agent,'name') else agent['name']
    tables={r['name'] for r in store.all("SELECT name FROM sqlite_master WHERE type='table'")}
    active=store.one('SELECT model FROM helper_sessions WHERE project=? AND agent=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1',proj['slug'],name) if 'helper_sessions' in tables else None
    helper=store.one('SELECT model FROM helpers WHERE project=? AND name=?',proj['slug'],name) if 'helpers' in tables else None
    model=((active['model'] if active else '') or (helper['model'] if helper else '') or '').strip()
    if model.lower() in {'auto','default','automatic'}:model=''
    return model


def identity(net, proj, agent):
    if agent is None:return '', 'legacy unattributed pauses'
    model=recorded_model(net,proj,agent)
    # Preserve explicit effort/provider qualifiers. Defaults do not identify a model.
    value='model:'+model if model else 'worker:'+str(agent.id if hasattr(agent,'id') else agent['id'])
    return ':'+hashlib.sha256(value.encode()).hexdigest(), model or 'this worker (model unspecified)'


def cooldown_start(net, proj, agent):
    """An explicit model switch must not inherit the previous model's release cooldown."""
    store = net.project_store(proj['id'])
    model=recorded_model(net,proj,agent)
    if not model:return 0
    tables={r['name'] for r in store.all("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'helper_sessions' not in tables:return 0
    name=agent.name if hasattr(agent,'name') else agent['name']
    row=store.one("SELECT MAX(COALESCE(ended_at,started_at,0)) boundary FROM helper_sessions WHERE project=? AND agent=? AND model!=? AND model!=''",proj['slug'],name,model)
    return row['boundary'] or 0
