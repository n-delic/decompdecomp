"""Durable project order and reconnection for batch scripts."""
import importlib.util
import json
from pathlib import Path
from urllib.error import URLError

spec = importlib.util.spec_from_file_location('project_batch_test', Path(__file__).resolve().parents[1] / 'tools/project_batch.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def test_checkpoint_preserves_order_and_resets(tmp_path):
    path = tmp_path / 'progress.json'
    projects = [{'slug': 'a'}, {'slug': 'b'}]
    state = batch.queue(path, projects, None)
    batch.finish_project(path, state)
    state = batch.queue(path, list(reversed(projects)), None)
    assert state['remaining'] == ['b']
    batch.finish_project(path, state)
    assert json.loads(path.read_text())['remaining'] == []
    assert batch.queue(path, projects, None)['remaining'] == ['a', 'b']


def test_reconnect_probes_before_retrying_post(tmp_path, monkeypatch):
    state = tmp_path / '.decomp' / 'server.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'port': 123, 'secret': 'x'}))
    calls = []

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, *args):
            return b'{}'

    def open_url(request, timeout):
        calls.append((request.full_url, request.get_method()))
        if len(calls) == 1:
            raise URLError('offline')
        return Response()

    monkeypatch.setattr(batch.urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(batch.time, 'sleep', lambda _: None)
    app = batch.App(tmp_path)
    try:
        app.request('/api/cross-game/run', {'slug': 'a'})
    except batch.Reconnected:
        pass
    else:
        raise AssertionError('expected a reconciliation request')
    assert calls == [('http://127.0.0.1:123/api/cross-game/run', 'POST'),
                     ('http://127.0.0.1:123/api/projects?brief=1', 'GET')]
