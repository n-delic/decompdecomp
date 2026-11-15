"""Import admission during a source update must explain refusals and release uploaded discs."""
import json
import time

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from decompnet.app import server, window
from decompnet.app.engine import RomNetwork
from decompnet.app.support import Jobs


@pytest.fixture
def app(tmp_path, monkeypatch):
    home = tmp_path / '.decomp'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'autopilot': False}))
    net = RomNetwork(home / 'decomp.db', tmp_path)
    jobs = Jobs()
    monkeypatch.setattr(server, 'Jobs', lambda *args: jobs)
    mcp, _, security, _ = server.build(net, home, 6497, 'import-test')
    client = TestClient(mcp.streamable_http_app(transport_security=security, host='127.0.0.1'),
                        base_url='http://127.0.0.1:6497',
                        headers={'User-Agent': f'Mozilla/5.0 {window.TOKEN_AGENT}import-test', 'X-Decomp': '1'})
    return client, jobs, home, net


def test_queued_update_rejects_import_before_receiving_the_disc(app, monkeypatch):
    client, jobs, home, _ = app
    jobs.source_update_pending = True

    async def unread(request):
        pytest.fail('A blocked import must not read the upload body.')
        yield b''

    monkeypatch.setattr(Request, 'stream', unread)
    response = client.post('/api/import?confirmed=1&name=game.iso', content=b'disc')
    assert response.status_code == 503
    assert 'source update is queued' in response.json()['error']
    assert not (home / 'uploads').exists()
    assert not jobs.jobs
    # The user must still be able to read/cancel the update and inspect existing work.
    assert client.get('/api/update/runtime').status_code == 200
    assert client.get('/api/projects').status_code == 200


@pytest.mark.parametrize('local', [False, True])
def test_update_queued_during_import_admission_cleans_only_uploaded_files(app, monkeypatch, tmp_path, local):
    client, jobs, home, _ = app
    original = jobs.start

    def queued(*args, **kwargs):
        jobs.source_update_pending = True
        return original(*args, **kwargs)

    monkeypatch.setattr(jobs, 'start', queued)
    game = tmp_path / 'owned.iso'
    game.write_bytes(b'disc')
    query = f'path={game}' if local else 'name=game.iso'
    response = client.post(f'/api/import?confirmed=1&{query}', content=b'disc')
    assert response.status_code == 503
    assert 'source update is queued' in response.json()['error']
    assert not jobs.jobs
    assert not (home / 'uploads').exists() or not list((home / 'uploads').iterdir())
    assert game.read_bytes() == b'disc'


def test_upload_after_the_update_hold_clears_starts_the_ordinary_import_job(app, monkeypatch):
    client, jobs, home, net = app
    jobs.source_update_pending = True
    assert client.post('/api/import?confirmed=1', content=b'disc').status_code == 503
    jobs.source_update_pending = False
    seen = []

    def imported(game, **kwargs):
        seen.append((game.name, game.read_bytes()))
        return 'imported-game'

    monkeypatch.setattr(net, 'import_rom', imported)
    response = client.post('/api/import?confirmed=1&name=game.iso', content=b'disc')
    assert response.status_code == 200
    job = jobs.jobs[response.json()['job']]
    deadline = time.monotonic() + 5
    while job['state'] == 'running' and time.monotonic() < deadline:
        time.sleep(.01)
    assert job['state'] == 'done', job
    assert seen == [('game.iso', b'disc')]
    assert not list((home / 'uploads').iterdir())
    assert jobs.jobs[response.json()['job']]['result'] == {'slug': 'imported-game'}
