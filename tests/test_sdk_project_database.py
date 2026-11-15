"""SDK selection writes to the same project owner used by the live app after migration."""

import json

import pytest
from starlette.testclient import TestClient

from decompnet.app import purpose_work, sdk_library, sdk_names
from decompnet.app.engine import RomNetwork
from decompnet.app.server import build
from decompnet.consoles.gc_wii.sdk import CORPUS

from .test_app_server import PORT, SECRET, WINDOW
from .test_project_database_migration import close


@pytest.fixture(params=[False, True], ids=['shared', 'project-database'])
def sdk_project(tmp_path, request, monkeypatch):
    net = RomNetwork(tmp_path/'catalog.db', tmp_path, project_databases=request.param)
    for slug in ('selected', 'other'):
        with net.project_registration(slug=slug, name=slug, description='', arch='ppc', big_endian=True,
                                      root=tmp_path/slug, config={'kind':'fork', 'platform':'gc', 'ready':False}) as (db, pid):
            pass
    with net.store.tx() as db:
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                   ('Local SDK','ppc',1,'SDKExample','example',40))
    monkeypatch.setattr(net.twins, 'prints', lambda proj: {})
    yield net
    close(net)


@pytest.mark.parametrize('selection', [None, [], [CORPUS], ['Local SDK']])
def test_library_selection_uses_current_owner_preserving_other_settings(sdk_project, selection):
    net = sdk_project
    store = net.project_store('selected')
    # The catalog can contain older settings; the owner's latest configuration must win.
    with store.tx() as db:
        row = db.execute("SELECT config FROM projects WHERE slug='selected'").fetchone()
        config = json.loads(row['config']) | {'unrelated_setting':'keep me'}
        db.execute("UPDATE projects SET config=? WHERE slug='selected'", (json.dumps(config),))
    other = net.project_store('other').one("SELECT config FROM projects WHERE slug='other'")['config']
    sdk_library.choose(net, 'selected', selection)
    saved = json.loads(store.one("SELECT config FROM projects WHERE slug='selected'")['config'])
    assert saved == config | {'sdk_libraries': selection}
    assert sdk_library.state(net, 'selected')['selection'] == selection
    assert net.project_store('other').one("SELECT config FROM projects WHERE slug='other'")['config'] == other
    # (choosing adds nothing; the N64's built-in table, loaded when it's read, isn't the game's choice)
    assert net.store.one("SELECT COUNT(*) count FROM sdk_signatures WHERE corpus NOT LIKE 'built in / %'")['count'] == 1


def test_manual_trigger_route_saves_selection_before_scheduling_sdk_scan(sdk_project, monkeypatch):
    net = sdk_project
    home = net.workspace/'.decomp';home.mkdir(exist_ok=True)
    mcp, manager, security, _ = build(net, home, PORT, SECRET)
    net.autosave.close()
    net.startup.close()
    calls = []
    monkeypatch.setattr(purpose_work, 'start', lambda title, project, fn, *args: calls.append((title, project, fn, args)))
    try:
        with TestClient(mcp.streamable_http_app(transport_security=security, host='127.0.0.1'),
                        base_url=f'http://127.0.0.1:{PORT}', headers=WINDOW) as client:
            payload = {'slug':'selected', 'selection':None}
            assert client.post('/api/sdk-library', json=payload).status_code == 403
            headers = {'X-Decomp':'1'}
            invalid = client.post('/api/sdk-library', json=payload | {'selection':['unknown']}, headers=headers)
            assert invalid.status_code == 400 and not calls
            response = client.post('/api/sdk-library', json=payload, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()['selection'] is None
            saved = net.project_store('selected').one("SELECT config FROM projects WHERE slug='selected'")
            assert 'sdk_libraries' in json.loads(saved['config'])
            assert calls == [('SDK function naming','selected',sdk_names.name_functions,(net,'selected'))]
            assert client.get('/api/sdk-library?slug=selected').status_code == 200
    finally:
        manager.shutdown()
        net.deep_sweeps.shutdown()
