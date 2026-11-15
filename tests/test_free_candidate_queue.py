"""Cheap discovery must not eagerly decode payloads for ineligible queued functions."""
from types import SimpleNamespace

import pytest

from decompnet import payload_storage
from decompnet.app import free_candidates as free
from decompnet.app import wrapper_families
from decompnet.app.twins import Job

from .test_similar_matches import pair as pair_fixture

pair = pair_fixture


def test_queue_without_fingerprints_does_not_decode_any_saved_candidate(pair, monkeypatch):  # noqa: F811
    from decompnet.app import cross_game_progress
    proj = pair._project('destination')
    monkeypatch.setattr(pair, 'project_row', lambda *_: proj)
    with pair.store.tx() as db:
        for i in range(300):
            db.execute("INSERT INTO functions(project_id,name,status,size,seed,best_source,best_score) "
                       "VALUES(2,?,'open',32,?,?,1)", (f'queued_{i}',f'machine {i} '*100,f'candidate {i} '*100))
    decoded = []
    def unpack(digest, blob):
        decoded.append(digest)
        return payload_storage.unpack(digest, blob)
    pair.project_store(2)._reader().create_function('payload_read',2,unpack)
    monkeypatch.setattr(wrapper_families, 'index', lambda *_: {})
    monkeypatch.setattr(pair.twins, 'prints', lambda *_: {})
    monkeypatch.setattr(cross_game_progress, 'report', lambda *args, **kwargs: None)
    monkeypatch.setattr(pair, 'try_quietly', lambda *_: pytest.fail('ineligible queue reached judge'))
    assert free.run(pair.twins,'destination',Job()) == 0
    assert decoded == []


def test_expired_budget_skips_indexes_and_candidate_queue(pair, monkeypatch):  # noqa: F811
    job = Job()
    job.reuse_deadline = 0
    job.reuse_limit = 10
    job.reuse_attempts = 0
    monkeypatch.setattr(wrapper_families,'index',lambda *_: pytest.fail('expired work built wrapper index'))
    monkeypatch.setattr(pair.twins,'prints',lambda *_: pytest.fail('expired work scanned fingerprints'))
    assert free.run(pair.twins,'destination',job) == 0


@pytest.mark.parametrize('change', ['matched', 'removed', 'deleted'])
def test_discovered_row_is_rechecked_before_loading_candidate(pair, monkeypatch, change):  # noqa: F811
    from decompnet.app import cross_game_progress
    monkeypatch.setattr(wrapper_families,'index',lambda *_: {})
    monkeypatch.setattr(pair.twins,'prints',lambda *_: {2:SimpleNamespace(key='fingerprint',asm=False)})
    changed = []
    def report(*args, **kwargs):
        if changed:return
        changed.append(1)
        with pair.store.tx() as db:
            if change == 'matched':db.execute("UPDATE functions SET status='matched' WHERE id=2")
            elif change == 'removed':db.execute('UPDATE functions SET gone_mark=1 WHERE id=2')
            else:db.execute('DELETE FROM functions WHERE id=2')
    monkeypatch.setattr(cross_game_progress,'report',report)
    monkeypatch.setattr(wrapper_families,'candidates',lambda *_: pytest.fail('stale row proposed a wrapper'))
    monkeypatch.setattr(pair,'try_quietly',lambda *_: pytest.fail('stale row reached judge'))
    assert free.run(pair.twins,'destination',Job()) == 0
    assert changed
