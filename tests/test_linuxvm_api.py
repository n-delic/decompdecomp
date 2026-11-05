"""The VM runner starts only through a protected, explicit background setup job."""
from decompnet import linuxvm
from decompnet.app.support import Jobs

from .test_app_server import app  # noqa: F401  shared isolated server fixture


def test_vm_state_is_read_only_and_setup_requires_the_write_guard(app, monkeypatch):  # noqa: F811
    client, _, _ = app
    calls = []
    monkeypatch.setattr(linuxvm, 'wanted', lambda: True)
    monkeypatch.setattr(linuxvm, 'status', lambda: (False, 'Prepare the local Linux runner.'))
    monkeypatch.setattr(linuxvm, 'setup', lambda **_: calls.append('setup'))
    def start(self, kind, run):
        calls.append(kind)
        run(lambda *_: None)
        return 7
    monkeypatch.setattr(Jobs, 'start', start)
    state = client.get('/api/state').json()
    assert state['linux_vm'] == {'ready': False, 'why': 'Prepare the local Linux runner.'}
    assert calls == []
    assert client.post('/api/linux-vm', json={}).status_code == 403
    assert calls == []
    response = client.post('/api/linux-vm', json={}, headers={'X-Decomp': '1'})
    assert response.status_code == 200 and response.json() == {'job': 7}
    assert calls == ['linux-vm', 'setup']
