"""Slow preparation must not serialize other projects or apply stale imports."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app import saves

from .test_progress import project  # noqa: F401


def test_slow_merge_comparison_leaves_shared_compiler_lane_available(project, monkeypatch):  # noqa: F811
    net, folder, judged = project
    (folder/'src').mkdir(exist_ok=True)
    (folder/'src/f.c').write_text('good imported')
    entered, finish = threading.Event(), threading.Event()
    render = saves._render
    def slow(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert finish.wait(4)
        return render(*args, **kwargs)
    monkeypatch.setattr(saves, '_render', slow)
    worker = net.progress
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(worker.sync, 'game', force=True)
        try:
            assert entered.wait(3)
            assert worker.verification_owner is None
            # Another project can start judging while the first plans its merge.
            assert worker.verification_slot.acquire(blocking=False)
            worker.verification_slot.release()
            assert not judged
        finally:finish.set()
        assert future.result(timeout=5)
    assert judged == [('f', 'good imported')]
    assert worker.verification_owner is None


@pytest.mark.parametrize('change', ['source', 'header', 'database', 'published-baseline', 'config'])
def test_waiting_import_discards_prepared_state_when_inputs_change(project, monkeypatch, change):  # noqa: F811
    net, folder, judged = project
    worker = net.progress
    (folder/'src').mkdir(exist_ok=True)
    (folder/'src/f.c').write_text('good original')
    entered = threading.Event()
    slot = worker.verification_slot
    proj = net._project('game')
    pid = proj['id']
    plans = []
    original = worker._sync
    def plan(*args, **kwargs):
        plans.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(worker, '_sync', plan)
    class Slot:
        def acquire(self, *, blocking):
            entered.set()
            return slot.acquire(blocking=blocking)
        def release(self):slot.release()
    monkeypatch.setattr(worker, 'verification_slot', Slot())
    slot.acquire()
    def synchronize():
        with worker._project_lock('game'):
            return worker.sync('game', force=True)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(synchronize)
        try:
            assert entered.wait(3)
            assert not judged
            # Admission must release every acquisition, including an outer batch.
            assert worker._project_lock('game').acquire(timeout=2)
            try:
                if change == 'source':
                    (folder/'src/f.c').write_text('good replacement')
                elif change == 'header':
                    data = json.loads((folder/saves.MANIFEST).read_text())
                    (folder/'include'/data['headers']['']).write_text('broken header')
                elif change in ('database', 'published-baseline'):
                    with net.store.tx() as db:
                        db.execute("UPDATE functions SET status='matched',matched_source='good local g',"
                                   "best_source='good local g',best_score=0 WHERE name='g'")
                    if change == 'published-baseline':
                        # Simulate a completed independent publication by changing
                        # the acknowledged snapshot while this waiter has no lock.
                        baseline = worker._baseline(pid)
                        from dataclasses import replace
                        worker.baselines[pid] = replace(baseline, dirty_token='new acknowledgement')
                elif change == 'config':
                    cfg = json.loads(proj['config'])
                    cfg['instructions'] = 'new local instructions'
                    with net.store.tx() as db:
                        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
            finally:worker._project_lock('game').release()
        finally:slot.release()
        assert future.result(timeout=5)
    assert worker.verification_owner is None
    assert len(plans) >= 2  # The entire old merge was discarded, not just its source row.
    fn = net._function('f', 'game')
    if change == 'source':
        assert fn['matched_source'] == 'good replacement'
        assert ('f', 'good original') not in judged
    elif change == 'header':
        assert fn['status'] != 'matched'
        assert net._context(fn['project_id'])['header'] == 'broken header'
    elif change in ('database', 'published-baseline'):
        assert net._function('g', 'game')['matched_source'] == 'good local g'
        assert (folder/'src/g.c').read_text().strip() == 'good local g'
    elif change == 'config':
        assert net.instructions('game') == 'new local instructions'
    assert slot.acquire(blocking=False)
    slot.release()


def test_cancelled_admission_retains_candidate_and_does_not_release_other_owner(project, monkeypatch):  # noqa: F811
    from decompnet.network import NetworkError
    net, folder, judged = project
    worker = net.progress
    (folder/'src').mkdir(exist_ok=True)
    (folder/'src/f.c').write_text('good retained')
    entered, cancel = threading.Event(), threading.Event()
    slot = worker.verification_slot
    class Slot:
        def acquire(self, *, blocking):
            entered.set()
            return slot.acquire(blocking=blocking)
        def release(self):slot.release()
    class Log:
        def __call__(self, text):pass
        def check_cancel(self):
            if cancel.is_set():raise NetworkError('Synthetic cancelled import')
    monkeypatch.setattr(worker, 'verification_slot', Slot())
    slot.acquire()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(worker.sync, 'game', force=True, logger=Log())
        try:
            assert entered.wait(3)
            cancel.set()
            with pytest.raises(NetworkError, match='Synthetic cancelled import'):
                future.result(timeout=3)
            assert not slot.acquire(blocking=False)
        finally:slot.release()
    assert not judged
    assert (folder/'src/f.c').read_text() == 'good retained'
    assert worker.verification_owner is None
    assert slot.acquire(blocking=False)
    slot.release()
