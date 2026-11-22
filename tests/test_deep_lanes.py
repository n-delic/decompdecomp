"""A deep pass's turns in child processes: the same errors and results as in the app, lanes reused, fallbacks."""
import json
import threading
from pathlib import Path

import pytest

from decompnet.app import deep_lanes
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


class Log:
    compile_pool = None

    def __init__(self):
        self.lines = []

    def __call__(self, line):
        self.lines.append(line)

    def progress(self, value):
        pass

    def check_cancel(self):
        pass


@pytest.fixture
def net(tmp_path):
    net = RomNetwork(str(tmp_path / 'app.db'), Path(tmp_path))
    (tmp_path / 'game.z64').write_bytes(b'\0' * 64)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True, 'rom': {'file': 'game.z64', 'sha1': '0' * 40},
           'segments': [], 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(1,'game','Game','mips',1,?,?)",
                   (str(tmp_path), json.dumps(cfg)))
    yield net
    net.store.conn.close()


def test_a_childs_error_reaches_the_pass_and_the_lane_is_reused(net):
    lanes = deep_lanes.Lanes(net, 2, 4)
    try:
        for _ in range(2):
            with pytest.raises(NetworkError):
                lanes.search('game', 'no_such_function', 1, 10, Log())  # the child's own validation, as in the app
        assert lanes.started == 1 and lanes.idle.qsize() == 1  # one child, kept for the next turn
    finally:
        lanes.close()


def test_turns_run_side_by_side_in_separate_children(net):
    lanes = deep_lanes.Lanes(net, 2, 4)
    errors = []
    barrier = threading.Barrier(2)

    def turn():
        barrier.wait()
        try:
            lanes.search('game', 'missing', 1, 10, Log())
        except NetworkError as error:
            errors.append(error)
    threads = [threading.Thread(target=turn) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        assert len(errors) == 2 and lanes.started <= 2
    finally:
        lanes.close()


def test_a_lane_that_cannot_start_leaves_the_turn_to_the_app(net, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError('no processes left')
    monkeypatch.setattr(deep_lanes, 'Lane', broken)
    lanes = deep_lanes.Lanes(net, 2, 4)
    with pytest.raises(deep_lanes.Unavailable):
        lanes.search('game', 'f', 1, 10, Log())
    with pytest.raises(deep_lanes.Unavailable):
        lanes.search('game', 'f', 1, 10, Log())  # remembered: no second attempt per turn


def test_lanes_are_off_when_disabled(monkeypatch):
    monkeypatch.setenv('DECOMP_DEEP_LANES', '0')
    assert not deep_lanes.enabled()
    monkeypatch.setenv('DECOMP_DEEP_LANES', '1')
    assert deep_lanes.enabled()


def test_each_lane_gets_its_share_of_the_machine(net, monkeypatch):
    monkeypatch.setattr(deep_lanes.os, 'cpu_count', lambda: 18)
    assert deep_lanes.Lanes(net, 24, 256).slots == 4  # not 256 // 24 = 10 compile threads each
    assert deep_lanes.Lanes(net, 4, 256).slots == 18
    assert deep_lanes.Lanes(net, 4, 8).slots == 2  # a smaller configured total still wins


def test_idle_lanes_are_trimmed_and_started_again_on_demand(net):
    """#leak: a looping pass that waited for scored C kept every idle lane, each with its proposal workers (about 70
    idle processes in one app). Lanes idle past the limit close, the count stays bounded, and a new turn starts one."""
    lanes = deep_lanes.Lanes(net, 3, 6)
    barrier = threading.Barrier(3)

    def turn():
        barrier.wait()
        with pytest.raises(NetworkError):
            lanes.search('game', 'no_such_function', 1, 10, Log())

    class Cancelled(Log):
        def check_cancel(self):
            raise KeyboardInterrupt

    try:
        threads = [threading.Thread(target=turn) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with pytest.raises((KeyboardInterrupt, NetworkError)):  # a cancelled turn, whatever its lane answered first
            lanes.search('game', 'no_such_function', 1, 10, Cancelled())
        assert lanes.started <= 3
        children = []
        while not lanes.idle.empty():
            children.append(lanes.idle.get_nowait())
        for lane in children:
            lanes.idle.put(lane)
        lanes.trim()  # (just used: kept)
        assert lanes.idle.qsize() == len(children)
        lanes.trim(0)
        assert lanes.started == 0 and lanes.idle.qsize() == 0
        assert all(lane.process.poll() is not None for lane in children)  # the children (and their workers) ended
        with pytest.raises(NetworkError):  # started again when a turn needs one
            lanes.search('game', 'no_such_function', 1, 10, Log())
        assert lanes.started == 1
    finally:
        lanes.close()
    assert lanes.started == 0


@pytest.mark.parametrize('disconnect', [False, True])
def test_remote_metrics_are_forwarded_and_gauges_clear_when_turn_ends(net, monkeypatch, disconnect):
    from queue import Queue
    from types import SimpleNamespace

    from decompnet.app.deep_throughput import Metrics

    metrics = Metrics()
    snapshots = []
    log = Log()
    log.compile_pool = SimpleNamespace(metrics=metrics)
    log.refresh_metrics = lambda: snapshots.append(metrics.snapshot())

    class Lane:
        def __init__(self):
            self.messages = Queue()

        def send(self, request):
            ident = request['ident']
            self.messages.put({'ident': ident, 'kind': 'metrics', 'live': {'executing': 2, 'waiting_search': 1},
                               'values': {'rank_literals_cpu_ns': 1_000_000, 'rank_batches': 1}})
            self.messages.put(None if disconnect else {'ident': ident, 'kind': 'reply', 'result': {}})

        def alive(self):
            return True

        def close(self):
            pass

    lanes = deep_lanes.Lanes(net, 2, 4)
    monkeypatch.setattr(lanes, '_take', lambda _: Lane())
    if disconnect:
        with pytest.raises(deep_lanes.Unavailable):
            lanes.search('game', 'f', 1, 10, log)
    else:
        assert lanes.search('game', 'f', 1, 10, log) == {}
    assert [v['active_compiles'] for v in snapshots] == [2, 0]
    assert [v['compiler_waiting_search'] for v in snapshots] == [1, 0]
    assert [v['rank_literals_cpu_seconds'] for v in snapshots] == [.001, .001]


def test_lanes_share_four_compile_slots_per_core_unless_a_number_is_set(net, monkeypatch):
    monkeypatch.setattr(deep_lanes.os, 'cpu_count', lambda: 18)
    assert deep_lanes.Lanes(net, 12, 0, maximum=True).slots == 6  # automatic: the lane window can reach its depth
    assert deep_lanes.Lanes(net, 27, 0, maximum=True).slots == 4
    assert deep_lanes.Lanes(net, 12, 24, maximum=True).slots == 2  # a number the user chose is split between lanes
