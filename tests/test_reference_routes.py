"""ROM-reference setup is a scoped, stoppable and deduplicated job."""
import threading
import time

from tests.test_app_server import app  # noqa: F401


def test_reference_setup_job_is_scoped_and_reconnectable(app, tmp_path, monkeypatch):  # noqa: F811
    client, net, _ = app
    root, exported = tmp_path/'public', tmp_path/'export'
    root.mkdir()
    exported.mkdir()
    net.forks._register('public', 'Public', 'fork', root,
                        {'kind':'fork', 'platform':'n64', 'game_sha1':'1'*40, 'ready':False}, 'x')
    entered, release = threading.Event(), threading.Event()
    def setup(slug, path, log):
        assert slug == 'public' and path == exported
        entered.set()
        assert release.wait(5)
        log.check_cancel()
        return {'slug':slug}
    monkeypatch.setattr(net.forks, 'adopt_rom_reference', setup)
    monkeypatch.setattr(net.forks, 'needs', lambda slug:{'rom_reference_available':True})
    headers = {'X-Decomp':'1'}
    data = {'slug':'public', 'path':str(exported)}
    try:
        first = client.post('/api/fork/rom-reference', headers=headers, json=data)
        assert first.status_code == 200, first.text
        job = first.json()['job']
        assert entered.wait(2)
        duplicate = client.post('/api/fork/rom-reference', headers=headers, json=data)
        assert duplicate.json()['job'] == job
        needs = client.get('/api/fork/needs', params={'slug':'public'}).json()
        assert needs['rom_reference_job']['id'] == job
        assert client.get('/api/job', params={'id':job}).json()['activity_project'] == 'public'
        assert client.post('/api/job/cancel', headers=headers, json={'id':job}).status_code == 200
    finally:
        release.set()
    for _ in range(100):
        state = client.get('/api/job', params={'id':job}).json()['state']
        if state != 'running':
            break
        time.sleep(.01)
    assert state == 'cancelled'


def test_open_original_checkout_only_within_its_workspace(app, tmp_path, monkeypatch):  # noqa: F811
    from decompnet import hostenv

    client, net, _ = app
    checkout = tmp_path/'checkout'
    workspace = checkout/'.decomp-wtf'/'rom-reference-workspaces'/'owned'
    workspace.mkdir(parents=True)
    net.forks._register('original-checkout-test', 'Public', 'fork', workspace,
                        {'kind':'fork', 'platform':'n64', 'ready':True,
                         'rom_reference':{'checkout':str(checkout)}}, 'x')
    opened = []
    monkeypatch.setattr(hostenv, 'open_folder', lambda path: opened.append(path))
    headers = {'X-Decomp':'1'}
    response = client.post('/api/open', headers=headers, json={'slug':'original-checkout-test', 'original':True})
    assert response.json()['ok']
    assert opened == [checkout.resolve()]
    net._update_cfg('original-checkout-test', lambda cfg: cfg['rom_reference'].update(checkout=str(tmp_path/'outside')))
    (tmp_path/'outside').mkdir()
    response = client.post('/api/open', headers=headers, json={'slug':'original-checkout-test', 'original':True})
    assert not response.json()['ok']
    assert opened == [checkout.resolve()]
