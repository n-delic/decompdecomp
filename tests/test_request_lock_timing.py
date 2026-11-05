"""Lock diagnostics distinguish acquisition from work and preserve transaction/claim semantics."""
import threading

import pytest

from decompnet import request_timing
from decompnet.store import Store
from tests.test_claims import _game


class DelayedLock:
    def __init__(self,clock,delay):
        self.clock=clock;self.delay=delay;self.lock=threading.RLock();self.depth=0

    def __enter__(self):
        self.lock.acquire();self.clock[0]+=self.delay;self.depth+=1
        return self

    def __exit__(self,*args):
        self.depth-=1;self.lock.release()


def test_acquisition_only_is_reported_and_exceptions_release_lock(monkeypatch):
    clock=[0.];lock=DelayedLock(clock,.25)
    monkeypatch.setattr(request_timing.time,'monotonic',lambda:clock[0])
    with request_timing.capture() as metrics:
        with pytest.raises(ValueError):
            with request_timing.timed_lock(lock,'candidate_lock_wait'):
                clock[0]+=100
                raise ValueError('Synthetic failure')
        assert metrics['candidate_lock_wait']==.25
    assert lock.depth==0
    # Disabled diagnostics do not consult the clock or create a capture.
    monkeypatch.setattr(request_timing.time,'monotonic',lambda:pytest.fail('No timer outside capture'))
    with request_timing.timed_lock(threading.RLock(),'candidate_lock_wait'):
        pass


def test_transaction_waits_do_not_count_held_time_and_rollback_is_unchanged(tmp_path,monkeypatch):
    store=Store(tmp_path/'scratch.db');clock=[0.]
    store.lock=DelayedLock(clock,.25)
    monkeypatch.setattr(request_timing.time,'monotonic',lambda:clock[0])
    with request_timing.capture() as metrics:
        with pytest.raises(ValueError):
            with store.tx() as db:
                db.execute("INSERT INTO settings VALUES('first','1')")
                clock[0]+=10
                with store.tx() as nested:
                    assert nested is db
                    nested.execute("INSERT INTO settings VALUES('second','2')")
                raise ValueError('Rollback both nested writes')
        assert metrics['write_lock_wait']==.5
    assert store.lock.depth==0
    assert store.one("SELECT 1 FROM settings WHERE key IN ('first','second')") is None


def test_candidate_and_writer_waits_are_separate_and_claim_limits_still_run(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',2,'open')]);agent,_=net.local_agent('a');fn=net._function('f','game')
    clock=[0.];net.store.lock=DelayedLock(clock,.25)
    candidate=DelayedLock(clock,.75);net._attempt_locks[agent.id,fn['id']]=candidate
    checked=[]
    monkeypatch.setattr(net,'check_attempt_limit',lambda *a:checked.append(1))
    monkeypatch.setattr(request_timing.time,'monotonic',lambda:clock[0])
    with request_timing.capture() as metrics:
        with net.attempt_turn(agent,fn):
            clock[0]+=20
        assert metrics['write_lock_wait']==.25
        assert metrics['candidate_lock_wait']==.75
    assert checked==[1] and candidate.depth==0 and net.store.lock.depth==0
