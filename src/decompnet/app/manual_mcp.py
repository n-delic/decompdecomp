"""User-created MCP credentials: one project per connection, no plaintext tokens saved."""
import uuid

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


def revoke(settings, agent_id):
    settings.data['manual_mcp'] = [item for item in connections(settings) if item['id'] != agent_id]
    settings.save()
