"""Users rename a function themselves, through the same checked rename helpers use.

Only helpers could rename, so a user told to rename one of two functions whose names differ only in capitals (an
N64 game's saves stopped over such a pair) had no way to do it from the app."""
import shutil
import subprocess

import pytest

from . import test_project_functions

net = test_project_functions.net
add = test_project_functions.add


@pytest.fixture
def client(net, tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from decompnet.app.server import build
    from tests.test_app_server import PORT, SECRET, WINDOW

    add(net, 1, 'local', bytes.fromhex('24020001240300022404000303e0000800000000'))
    home = tmp_path/'.decomp'
    home.mkdir()
    mcp, _, security, _ = build(net, home, PORT, SECRET)
    return TestClient(mcp.streamable_http_app(transport_security=security, host='127.0.0.1'),
                      base_url=f'http://127.0.0.1:{PORT}', headers=WINDOW)


def test_rename_route_renames_with_the_typed_name(net, client, monkeypatch):
    renamed = []
    monkeypatch.setattr(net, 'rename_symbol', lambda *args, **kwargs: renamed.append(args) or '✅ renamed')
    body = {'slug': '1', 'function': 'local', 'name': 'decode_packet'}
    assert client.post('/api/rename-function', json=body).status_code == 403  # (a write needs the window's header)
    response = client.post('/api/rename-function', json=body, headers={'X-Decomp': '1'})
    assert response.status_code == 200 and response.json()['message'] == '✅ renamed'
    assert renamed[0][1:4] == ('1', 'local', 'decode_packet')


@pytest.mark.parametrize('name,error', [('', "Enter the function's new name."), ('local', 'already has that name')])
def test_rename_route_refuses_an_empty_or_unchanged_name(net, client, monkeypatch, name, error):
    monkeypatch.setattr(net, 'rename_symbol', lambda *args, **kwargs: pytest.fail('nothing to rename'))
    response = client.post('/api/rename-function', json={'slug': '1', 'function': 'local', 'name': name},
                           headers={'X-Decomp': '1'})
    assert response.status_code == 400 and error in response.json()['error']


def test_rename_route_reports_a_refused_rename(net, client, monkeypatch):
    monkeypatch.setattr(net, 'rename_symbol', lambda *args, **kwargs: '❌ the matches that call it no longer match')
    response = client.post('/api/rename-function', json={'slug': '1', 'function': 'local', 'name': 'x_name'},
                           headers={'X-Decomp': '1'})
    assert response.status_code >= 400 and 'no longer match' in response.json()['error']


def test_rename_route_runs_as_a_job_with_progress(net, client, monkeypatch):
    def rename(*args, progress=None, **kwargs):
        if progress:
            progress({'done': 1, 'total': 1})
        return '✅ renamed'
    monkeypatch.setattr(net, 'rename_symbol', rename)
    started = client.post('/api/rename-function', json={'slug': '1', 'function': 'local', 'name': 'x_name',
                                                        'background': True}, headers={'X-Decomp': '1'})
    assert started.status_code == 200 and started.json()['job']


def test_rename_dialog_gui():
    if not (node := shutil.which('node')):
        pytest.skip('needs node')
    subprocess.run([node, 'tests/gui_rename_function.js'], check=True, capture_output=True, text=True, timeout=20)
