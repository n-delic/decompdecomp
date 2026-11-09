"""User-created MCP credentials: one project per connection, no plaintext tokens saved."""
import uuid
from contextlib import ExitStack

from ..network import NetworkError


def connections(settings):
    return settings.data.get('manual_mcp', [])


def project_for(net, settings, agent_id):
    for item in connections(settings):
        if item['id'] == agent_id:
            try:
                net.project_row(item['slug'])
            except NetworkError:
                return None
            return item['slug']
    return None


def create(net, settings, slug):
    if not slug:
        raise NetworkError('Choose a project for this connection.')
    net.project_row(slug)
    name = 'manual-' + uuid.uuid4().hex[:16]
    token = net.register_agent(name, 'you', origin='manual-mcp')
    agent = net.authenticate(token)
    item = {'id': agent.id, 'name': name, 'slug': slug}
    settings.data['manual_mcp'] = [*connections(settings), item]
    settings.save()
    return {**item, 'token': token}


def revoke(net, settings, agent_id):
    """Invalidate first, drain in-flight attempts, then free only this connection's claims."""
    with net.store.lock:
        if not any(item['id']==agent_id for item in connections(settings)):
            return
        net.__dict__.setdefault('_revoked_manual_agents',set()).add(agent_id)
        locks=[lock for (aid,_),lock in net._attempt_locks.items() if aid==agent_id]
        settings.data['manual_mcp']=[item for item in connections(settings) if item['id']!=agent_id]
        settings.save()
    # Never hold the database lock while waiting for a compiler. Its accepted result
    # finishes first; already-authenticated queued submissions fail the revocation gate.
    with ExitStack() as pending:
        for lock in locks:pending.enter_context(lock)
        net.release_stopped([agent_id], 'its manual MCP connection was revoked')
