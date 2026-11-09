"""Timing totals are numeric, scoped to a run and safe across parallel workers."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from decompnet import compile_timing
from decompnet.app.autopilot_timing import Timings
from decompnet.app.engine import RomNetwork
from tests.test_loading import tiny_game


def test_timing_totals_are_thread_safe_and_filter_unknown_fields():
    timing=Timings()
    def worker(_):
        for _ in range(100):
            timing.add('generation',.25);timing.add('publication',.125)
            timing.add('source or path must not appear',99)
    with ThreadPoolExecutor(4) as pool:list(pool.map(worker,range(4)))
    snapshot=timing.snapshot()
    assert snapshot['generation']==100 and snapshot['database_writes']==50
    assert 'source or path must not appear' not in snapshot
    assert all(isinstance(value,(int,float)) for value in snapshot.values())


def test_live_timings_are_serializable_durable_and_reset_for_the_next_run(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'app.db',tmp_path);proj=tiny_game(net)
    ids=[row['id'] for row in net.store.all('SELECT id FROM functions')]
    monkeypatch.setattr(net.twins,'full_pass',lambda *a,**kw:SimpleNamespace(matched=0))
    def state():return dict(state='running',tried=0,matched=0,near=0,twins=0,stop=threading.Event(),_foreground=threading.Event())
    first=state();net.autopilots[proj['slug']]=first
    def trial(*a):
        net.store.one('SELECT id FROM functions LIMIT 1')
        compile_timing.add('generation',.5);compile_timing.add('setup',.25)
        view=net.autopilot_status(proj['slug'])
        assert view['timings']['generation']>0 and not any(key.startswith('_') for key in view)
        json.dumps(view)
        return 'far'
    net._autopilot_work(proj['slug'],ids,first,(1,1),trial)
    assert first['timings']['generation']==len(ids)*.5
    assert first['timings']['database_reads']>=0
    net.autopilots.clear()
    assert net.autopilot_status(proj['slug'])['timings']['preparation']==len(ids)*.25
    second=state();net.autopilots[proj['slug']]=second
    net._autopilot_work(proj['slug'],ids,second,(1,1),lambda *a:'far')
    assert second['timings']['generation']==0


def test_database_read_adapter_keeps_totals_when_a_trial_raises():
    import pytest

    from decompnet import request_timing
    from decompnet.app.autopilot_timing import database_reads
    timing=Timings()
    with compile_timing.collect(timing),pytest.raises(RuntimeError):
        with database_reads():
            request_timing.sql_read(.25)
            raise RuntimeError('trial failed')
    assert timing.snapshot()['database_reads']==.25
