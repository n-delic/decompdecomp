"""The Autopilot batch follows each project through completion."""
import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
try:
    spec = importlib.util.spec_from_file_location('autopilot_all', TOOLS / 'autopilot_all.py')
    batch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(batch)
finally:
    sys.path.remove(str(TOOLS))


def test_runs_each_autopilot_in_order(monkeypatch):
    calls = []
    replies = iter([{'autopilot': None}, {'autopilot': {'state': 'running'}},
                    {'autopilot': {'state': 'done'}}, {'autopilot': {'state': 'running'}},
                    {'autopilot': {'state': 'done'}}])

    def call(info, path, body=None):
        calls.append((path, body))
        return next(replies)

    monkeypatch.setattr(batch, 'call', call)
    monkeypatch.setattr(batch.time, 'sleep', lambda _: None)
    assert batch.run({}, [{'slug': 'a'}, {'slug': 'b'}]) == []
    assert [path for path, _ in calls] == ['/api/project?slug=a', '/api/autopilot', '/api/project?slug=a',
                                           '/api/project?slug=b', '/api/project?slug=b']


def test_interrupt_stops_only_own_autopilot(monkeypatch):
    calls = []

    def call(info, path, body=None):
        calls.append((path, body))
        if path.startswith('/api/project'):
            if len(calls) == 1:
                return {'autopilot': None}
            raise KeyboardInterrupt
        return {'autopilot': {'state': 'running'}}

    monkeypatch.setattr(batch, 'call', call)
    monkeypatch.setattr(batch.time, 'sleep', lambda _: None)
    import pytest

    with pytest.raises(KeyboardInterrupt):
        batch.run({}, [{'slug': 'a'}])
    assert calls[-1] == ('/api/autopilot', {'slug': 'a', 'action': 'stop'})
