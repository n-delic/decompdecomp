"""Natural map/view deadlines identify their owner without disclosing request or source content."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app import view_snapshots as views
from decompnet.app.engine import RomNetwork
from decompnet.read_cache import ReadPending
from tests.test_loading import tiny_game


def test_map_timeout_logs_owner_once_and_retains_retry_and_cleanup(tmp_path, monkeypatch, caplog):
    net = RomNetwork(tmp_path/'db', tmp_path)
    tiny_game(net, 'private-slug')
    entered, release = threading.Event(), threading.Event()
    def build(slug):
        private_source = 'proprietary-source'
        authorization = 'Bearer private-credential'
        assert slug == 'private-slug' and private_source and authorization
        entered.set()
        assert release.wait(5)
        return []
    monkeypatch.setattr(net, 'function_map', build)
    monkeypatch.setattr(views, 'SNAPSHOT_WAIT_SECONDS', .01)
    def read():
        with net.progress.view():return views.incremental_map(net, 'private-slug')
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    with ThreadPoolExecutor(1) as pool:
        owner = pool.submit(read)
        try:
            assert entered.wait(5)
            for _ in range(3):
                with pytest.raises(ReadPending, match='function map is still being prepared'):
                    read()
            messages = [r.message for r in caplog.records if 'Project view preparation busy' in r.message]
            assert len(messages) == 1
            assert 'lane=map' in messages[0] and 'phase=building full map' in messages[0]
            assert 'view_snapshots.py:incremental_map' in messages[0]
            for secret in ('private-slug', 'proprietary-source', 'Bearer', 'private-credential', str(tmp_path)):
                assert secret not in messages[0]
            assert len(messages[0]) < 1400
        finally:release.set()
        assert owner.result(timeout=5) == []
    cache = views.cache(net)
    assert not cache.map_owners and not next(iter(cache.map_locks.values())).locked()


def test_shared_snapshot_timeout_logs_builder_without_its_key(monkeypatch, caplog):
    cache = views.Snapshots()
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(views, 'SNAPSHOT_WAIT_SECONDS', .01)
    def build():
        entered.set()
        assert release.wait(5)
        return ['fresh']
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    with ThreadPoolExecutor(1) as pool:
        owner = pool.submit(cache.get, 'private-query-key', build)
        try:
            assert entered.wait(5)
            for _ in range(2):
                with pytest.raises(ReadPending):cache.get('private-query-key', build)
            messages = [r.message for r in caplog.records if 'Project view preparation busy' in r.message]
            assert len(messages) == 1 and 'lane=snapshot' in messages[0]
            assert 'view_snapshots.py:get' in messages[0]
            assert 'private-query-key' not in messages[0]
        finally:release.set()
        assert owner.result(timeout=5) == ['fresh']
    assert not cache.pending


def test_map_builder_error_releases_owner_and_lock(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path/'db', tmp_path)
    tiny_game(net)
    def fail(slug):raise RuntimeError('synthetic builder failure')
    monkeypatch.setattr(net, 'function_map', fail)
    with net.progress.view(), pytest.raises(RuntimeError, match='synthetic builder failure'):
        views.incremental_map(net, 'game')
    cache = views.cache(net)
    assert not cache.map_owners and not next(iter(cache.map_locks.values())).locked()
    monkeypatch.setattr(net, 'function_map', lambda slug: [])
    with net.progress.view():assert views.incremental_map(net, 'game') == []


def test_diagnostic_rate_bookkeeping_is_bounded(caplog):
    cache = views.Snapshots()
    caplog.set_level(logging.CRITICAL, logger='decompdecomp')
    for i in range(70):cache.busy('map', (-i-1, 0, 'building full map'))
    assert len(cache.busy_logged) == 64
