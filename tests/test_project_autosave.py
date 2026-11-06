"""Project autosave controls cancel background work without disabling explicit verification."""
import threading
import time
from types import SimpleNamespace

from decompnet.app import saves
from decompnet.app.support import Settings


def wait_for(predicate):
    until = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < until
        time.sleep(.01)


def test_project_workers_pause_resume_persist_and_share_one_slot(tmp_path):
    rows = [{'slug':slug, 'root':str(tmp_path), 'config':'{"kind":"rom","rom":{"sha1":"abc"}}'}
            for slug in ('a', 'b')]
    rows += [{'slug':'imported', 'root':str(tmp_path), 'config':'{"kind":"fork"}'}]
    entered = {slug:threading.Event() for slug in ('a','b')}
    release = threading.Event()
    lock = threading.Lock()
    counts = {'active':0, 'maximum':0, 'explicit':0}
    def sync(slug, **options):
        logger = options.get('logger')
        if logger is None:
            counts['explicit'] += 1
            return True
        with lock:
            counts['active'] += 1
            counts['maximum'] = max(counts['active'], counts['maximum'])
        entered[slug].set()
        try:
            while not release.wait(.01):logger.check_cancel()
            logger.check_cancel()
        finally:
            with lock:counts['active'] -= 1
        return True
    settings = Settings(tmp_path / 'settings.json')
    auto = saves.Autosave(SimpleNamespace(store=SimpleNamespace(all=lambda *_:rows),
                                         progress=SimpleNamespace(sync=sync)), settings)
    second = rows.pop(1)
    auto._reconcile()
    try:
        assert entered['a'].wait(2)
        rows.append(second)
        auto._reconcile()
        auto.pause('a')
        assert entered['b'].wait(2)  # pausing one releases the shared lane for the other project
        wait_for(lambda: next(t for t in auto.snapshot() if t['key']=='a')['state']=='paused')
        assert counts['maximum'] == 1
        assert {t['key'] for t in auto.snapshot()} == {'a','b'}
        assert all(t['thread_id'] for t in auto.snapshot())
        assert 'a' in saves.Autosave(auto.net, Settings(settings.path)).paused
        assert auto.run_once(rows=rows[:1]) == ['a']  # explicit save bypasses only background pause
        assert counts['explicit'] == 1
        auto.pause('b')
        wait_for(lambda: counts['active']==0)
        entered['a'].clear()
        auto.pause('a', False)
        assert entered['a'].wait(2)
        assert 'a' not in Settings(settings.path).data['autosave_paused']
        assert 'a' not in auto.retry_after
    finally:
        auto.close()
        for worker in list(auto.workers.values()):worker['thread'].join(2)
    assert not any(w['thread'].is_alive() for w in auto.workers.values())


def test_project_removal_retires_worker_and_start_is_idempotent(tmp_path):
    rows = [{'slug':'game', 'root':str(tmp_path), 'config':'{"kind":"rom","rom":{"sha1":"abc"}}'}]
    net = SimpleNamespace(store=SimpleNamespace(all=lambda *_:list(rows)),
                          progress=SimpleNamespace(sync=lambda *args, **kw:False))
    auto = saves.Autosave(net)
    auto.start()
    try:
        wait_for(lambda:bool(auto.snapshot()))
        thread = auto.thread
        auto.start()
        assert auto.thread is thread
        old = auto.workers['game']['thread']
        rows.clear()
        auto._reconcile()
        assert not auto.snapshot()
        old.join(2)
        assert not old.is_alive()
    finally:
        auto.close()
        auto.thread.join(2)
