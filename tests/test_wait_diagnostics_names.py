"""Long-wait warnings say which metadata and which project, and nothing else about them."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet import db_diagnostics, read_cache
from decompnet.app.engine import RomNetwork
from tests.test_loading import tiny_game


def test_a_reader_that_gives_up_logs_the_key_once(monkeypatch, caplog):
    monkeypatch.setattr(read_cache, 'READ_WAIT_SECONDS', .01)
    monkeypatch.setattr(db_diagnostics, '_last', {})
    cache = read_cache.ReadCache()
    key = ('candidate-header', 7, 'src/unit_a.c', b'private bytes')
    entered, release = threading.Event(), threading.Event()

    def build():
        entered.set()
        assert release.wait(5)
        return 'fresh'
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    with ThreadPoolExecutor(1) as pool:
        owner = pool.submit(cache.get, key, 1, build)
        try:
            assert entered.wait(5)
            for _ in range(2):
                with pytest.raises(read_cache.ReadPending, match='Shared metadata is still being prepared'):
                    cache.get(key, 1, build)
        finally:
            release.set()
        assert owner.result(timeout=5) == 'fresh'
    messages = [r.getMessage() for r in caplog.records if 'Shared metadata wait' in r.getMessage()]
    assert messages == ['Shared metadata wait timed out: key=candidate-header,7,src/unit_a.c,<bytes>']


def test_a_logged_name_is_one_bounded_line():
    text = db_diagnostics.cache_key(('kind', 'a\nforged line', 'x' * 500))
    assert '\n' not in text and ' ' not in text and len(text) <= 240


def test_a_project_database_names_its_project_when_a_writer_waits(tmp_path, monkeypatch, caplog):
    tiny_game(RomNetwork(tmp_path / 'app.db', tmp_path), 'some-project')
    net = RomNetwork(tmp_path / 'app.db', tmp_path, project_databases=True)  # the upgrade moves it out
    store = net.project_store('some-project')
    assert store is not net.store and store.project_slug == 'some-project'
    assert net.store.project_slug is None
    monkeypatch.setattr(db_diagnostics, 'WRITE_WAIT_SECONDS', 0)
    monkeypatch.setattr(db_diagnostics, '_last', {})
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    with store.tx():
        pass
    with net.store.tx():
        pass
    messages = [r.getMessage() for r in caplog.records if r.getMessage().startswith('Database write waited')]
    assert any(m.endswith('project=some-project') for m in messages)
    assert any(m.endswith('project=-') for m in messages)
