"""Priority metadata must not wait for unrelated saved C to finish compiling."""
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from decompnet.app import saves
from tests.test_progress import project as project_fixture

project = project_fixture


@pytest.mark.parametrize('transport', ['engine', 'http'])
@pytest.mark.parametrize('on', [False, True])
def test_priority_returns_during_saved_recheck_and_is_published(project, monkeypatch, tmp_path, transport, on):
    net, folder, judged = project
    # A removal must really remove an existing priority.
    net._update_cfg('game', lambda cfg: cfg.update(priority=['g'] if not on else []), synchronize=False)
    net.progress.sync('game', force=True)
    invoke = lambda: net.set_priority('game', 'g', on)
    if transport == 'http':
        from starlette.testclient import TestClient

        from decompnet.app.server import build
        from tests.test_app_server import WINDOW
        home = tmp_path / '.decomp'
        home.mkdir(exist_ok=True)
        mcp, manager, security, _ = build(net, home, 6499, 's3cret-for-tests')
        net.autosave.close()
        client = TestClient(mcp.streamable_http_app(transport_security=security, host='127.0.0.1'),
                            base_url='http://127.0.0.1:6499', headers=WINDOW)
        def invoke():
            response = client.post('/api/priority', json={'slug': 'game', 'name': 'g', 'on': on},
                                   headers={'X-Decomp': '1'})
            assert response.status_code == 200, response.text
            return response.json()['priority']
    waiting, compilation = threading.Event(), Future()
    def checks(*args):
        waiting.set()
        yield 'f', {'matched_by': 'helper'}, 'src', 'good saved', 'f', ('compile', compilation)
    monkeypatch.setattr(saves, '_saved_checks', checks)
    (folder/'src').mkdir(exist_ok=True)
    (folder/'src/f.c').write_text('good saved')
    with ThreadPoolExecutor(2) as pool:
        background = pool.submit(net.progress.sync, 'game', force=True)
        try:
            assert waiting.wait(5)
            assert pool.submit(invoke).result(timeout=2) == (['g'] if on else [])
            assert not background.done() and not judged
            assert net.cfg(net.store.one("SELECT config FROM projects WHERE slug='game'"))['priority'] == (['g'] if on else [])
        finally:
            compilation.set_result(None)
        background.result(timeout=5)
    # Synchronization still owns import/verification and eventually saves the metadata.
    net.progress.sync('game', force=True)
    assert json.loads((folder/saves.MANIFEST).read_text())['priority'] == (['g'] if on else [])
    assert judged and net.project_store('game').one("SELECT status FROM functions WHERE name='f'")['status'] == 'matched'
