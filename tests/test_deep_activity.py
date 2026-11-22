"""Parallel activity remains attributed and bounded, independent of completion order."""
from concurrent.futures import ThreadPoolExecutor

from decompnet.app.deep_activity import Activity


def test_interleaved_workers_keep_their_own_output_and_finish_independently():
    snapshots = []
    class Log:
        def progress(self, value):
            snapshots.append(value)
    activity = Activity(Log())
    activity.publish({'matches': 3})
    def worker(name):
        for i in range(12):
            activity.update(name, {'tested': i, 'best_percent': 90+i/10}, line=f'{name}: {i}')
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(worker, ['a', 'b']))
    activity.publish({})  # Explicit flush reads the newest coalesced counters/output.
    rows = {w['function']: w for w in snapshots[-1]['workers']}
    assert rows.keys() == {'a', 'b'}
    assert all(len(w['lines']) == 6 for w in rows.values())
    assert all(line.startswith(name) for name, w in rows.items() for line in w['lines'])
    activity.update('a', {'phase': 'finished'}, finished=True)
    assert [w['function'] for w in snapshots[-1]['workers']] == ['b']
    assert snapshots[-1]['recent_workers'][0]['function'] == 'a'
    assert snapshots[-1]['matches'] == 3
    for i in range(20):
        activity.update(str(i), finished=True)
    assert len(snapshots[-1]['recent_workers']) == 8
    assert len(snapshots[1]['workers'][0]['lines']) == 1


def test_elapsed_time_is_measured_and_frozen_on_completion(monkeypatch):
    now = [100.0]
    monkeypatch.setattr('decompnet.app.deep_activity.time.monotonic', lambda: now[0])
    snapshots = []
    class Log:
        def progress(self, value):
            snapshots.append(value)
    activity = Activity(Log())
    activity.update('a')
    now[0] += 75
    activity.publish({})
    assert snapshots[-1]['workers'][0]['elapsed_seconds'] == 75
    activity.update('a', finished=True)
    now[0] += 50
    activity.publish({})
    assert snapshots[-1]['recent_workers'][0]['elapsed_seconds'] == 75
    assert snapshots[0]['workers'][0]['elapsed_seconds'] == 0
    activity.update('a')
    assert snapshots[-1]['workers'][0]['elapsed_seconds'] == 0


def test_bursts_coalesce_and_trailing_timer_flushes_latest(monkeypatch):
    timers, snapshots = [], []
    class Timer:
        def __init__(self, interval, callback):
            self.callback, self.cancelled = callback, False
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            self.cancelled = True
    monkeypatch.setattr('decompnet.app.deep_activity.threading.Timer', Timer)
    monkeypatch.setattr('decompnet.app.deep_activity.time.monotonic', lambda: 10.)
    class Log:
        def progress(self, value):
            snapshots.append(value)
    activity = Activity(Log())
    activity.update('a', line='first')
    for i in range(100):
        activity.update('a', {'tested':i}, line=str(i))
        activity.publish({'tested':i}, force=False)
    assert len(snapshots) == 1 and len(timers) == 1
    monkeypatch.setattr('decompnet.app.deep_activity.threading.current_thread', lambda: timers[0])
    timers[0].callback()
    assert len(snapshots) == 2
    assert snapshots[-1]['workers'][0]['tested'] == 99
    assert snapshots[-1]['workers'][0]['lines'] == list(map(str,range(94,100)))
    activity.update('a', {'improvements':1})
    assert len(snapshots) == 3
    activity.update('a', finished=True)
    assert snapshots[-1]['workers'] == []
    assert snapshots[-1]['recent_workers'][0]['tested'] == 99


def test_pass_throughput_supersedes_the_last_workers_snapshot():
    from types import SimpleNamespace

    from decompnet.app.deep_throughput import Metrics

    metrics = Metrics()
    metrics.remote_slots = 4
    metrics.set_remote(1, {'executing': 2, 'waiting_search': 1, 'proposal_workers': 1})
    metrics.set_remote(2, {'executing': 1, 'waiting_shared': 2})
    metrics.add('rank_literals_cpu_ns', 2_400_000)
    metrics.add('rank_batches', 2)
    metrics.add('rank_candidates', 16)
    snapshots = []
    activity = Activity(SimpleNamespace(compile_pool=SimpleNamespace(metrics=metrics), progress=snapshots.append))
    activity.publish({'throughput': {'active_compiles': 1, 'compile_slots': 2}})
    throughput = snapshots[-1]['throughput']
    assert (throughput['active_compiles'], throughput['compile_slots']) == (3, 4)
    assert (throughput['compiler_waiting_search'], throughput['compiler_waiting_shared']) == (1, 2)
    assert throughput['active_proposal_workers'] == 1
    assert throughput['rank_literals_cpu_seconds'] == .0024
    assert (throughput['rank_batches'], throughput['rank_candidates']) == (2, 16)
    metrics.set_remote(1, None)
    activity.publish({})
    assert snapshots[-1]['throughput']['active_compiles'] == 1
    assert snapshots[-1]['throughput']['rank_batches'] == 2
