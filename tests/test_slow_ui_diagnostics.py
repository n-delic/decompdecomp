"""Slow UI requests identify their route without exposing project query parameters."""
import logging
import time

from .test_app_server import app  # noqa: F401


def test_slow_ui_requests_log_route_without_parameters(app, monkeypatch, caplog):  # noqa: F811
    client, net, _ = app
    def slow_stats(_):
        time.sleep(.51)
        return {'agents': []}
    monkeypatch.setattr(net, 'stats', slow_stats)
    with caplog.at_level(logging.WARNING, logger='decompdecomp'):
        for _ in range(2):
            assert client.get('/api/stats?slug=private-project').status_code == 200
    records = [r.message for r in caplog.records if r.message.startswith('Slow UI request')]
    assert len(records) == 1
    assert '/api/stats' in records[0]
    assert 'private-project' not in records[0]
