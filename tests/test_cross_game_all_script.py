"""Batch search reattaches after lost responses and app restarts."""
import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
try:
    spec = importlib.util.spec_from_file_location('cross_game_all', TOOLS / 'cross_game_all.py')
    batch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(batch)
finally:
    sys.path.remove(str(TOOLS))


def test_reattaches_after_start_response_lost(monkeypatch):
    calls = []

    class App:
        def request(self, path, body=None):
            calls.append((path, body))
            if len(calls) == 1:
                return {'twins': {}}
            if len(calls) == 2:
                raise batch.Reconnected
            if len(calls) == 3:
                return {'twins': {'state': 'running', 'cross_job': 'existing'}}
            return {'state': 'done', 'result': {'matched': 1}}

    batch.run_project(App(), 'game', interval=0)
    assert [path for path, _ in calls] == ['/api/project?slug=game', '/api/cross-game/run',
                                           '/api/project?slug=game', '/api/job?id=existing']


def test_failure_does_not_finish_project():
    class App:
        def request(self, path, body=None):
            if path.startswith('/api/project'):
                return {'twins': {'state': 'running', 'cross_job': 'one'}}
            return {'state': 'error', 'error': 'bad project'}

    with pytest.raises(RuntimeError, match='bad project'):
        batch.run_project(App(), 'game', interval=0)
