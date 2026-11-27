"""Autopilot resumes its current project after the app restarts."""
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


def test_restarts_current_project_after_disconnect(monkeypatch):
    calls = []
    replies = iter([{'autopilot': None}, {'autopilot': {'state': 'running'}}, batch.Reconnected(),
                    {'autopilot': {'state': 'done', 'started_at': 1}},
                    {'autopilot': {'state': 'running'}}, {'autopilot': {'state': 'done'}}])

    class App:
        def request(self, path, body=None):
            calls.append((path, body))
            result = next(replies)
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(batch.time, 'sleep', lambda _: None)
    batch.run_project(App(), 'game', interval=0)
    assert sum(path == '/api/autopilot' for path, _ in calls) == 2
