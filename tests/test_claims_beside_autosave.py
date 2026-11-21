"""#claims-not-behind-permuter: a claim never queues behind the autosave's check of the saved folder.

While a Deep Permuter pass ran on an N64 game, its improvements kept the autosave worker publishing: the manifest kept
changing, so claim_function's narrow pre-check missed and fell back to a full check that waited for the project lock
behind the autosave's own (60-245 s each, claims up to 15 minutes). A thread holding that lock stands in for it here."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app import saves, tools
from tests.test_progress import project as project_fixture

project = project_fixture


def _autosave_holding_the_lock(net, folder):
    """The folder changed since the last check (the narrow check misses) and another thread holds the lock."""
    manifest = folder / saves.MANIFEST
    manifest.write_text(manifest.read_text() + '\n')
    held, release = threading.Event(), threading.Event()

    def autosave():
        with net.progress._project_lock('game'):
            held.set()
            release.wait(30)
    return autosave, held, release


@pytest.mark.parametrize('transport', ['direct', 'mcp'])
@pytest.mark.parametrize('function', ['', 'g'])
def test_claim_function_does_not_wait_for_the_autosaves_check(project, monkeypatch, transport, function):
    net, folder, _ = project
    agent, _ = net.local_agent('helper')
    args = {'function': function} if function else {}
    if transport == 'mcp':
        import anyio
        from mcp.server.mcpserver import Context
        monkeypatch.setattr(Context, 'headers', property(lambda self: {}))
        server = tools.build_mcp(net, lambda ctx: (agent, 'game'))

        async def request():
            return await server.call_tool('claim_function', args)

        def invoke():
            return str(anyio.run(request))
    else:
        def invoke():
            return tools.call(net, agent, 'game', 'claim_function', args)
    autosave, held, release = _autosave_holding_the_lock(net, folder)
    with ThreadPoolExecutor(2) as pool:
        holder = pool.submit(autosave)
        try:
            assert held.wait(5)
            reply = pool.submit(invoke).result(timeout=5)  # (before: waited for as long as the lock was held)
        finally:
            release.set()
        holder.result(timeout=5)
    assert 'CLAIMED' in reply
    assert net.store.one('SELECT COUNT(*) AS n FROM claims WHERE agent_id=?', agent.id)['n'] == 1


def test_a_claim_with_the_lock_free_still_reads_the_changed_folder(project, monkeypatch):
    net, folder, _ = project
    agent, _ = net.local_agent('helper')
    manifest = folder / saves.MANIFEST
    manifest.write_text(manifest.read_text() + '\n')
    forced = []
    sync = type(net.progress).sync
    monkeypatch.setattr(type(net.progress), 'sync',
                        lambda self, slug, **k: forced.append(k.get('force')) or sync(self, slug, **k))
    assert 'CLAIMED' in tools.call(net, agent, 'game', 'claim_function', {})
    assert True in forced
