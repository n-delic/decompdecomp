"""Draft worker leases survive cancellation and never reuse a replaced game's loaded program."""

import queue
from types import SimpleNamespace

import pytest

from decompnet.forks import ghidra_worker as workers
from decompnet.network import NetworkError


@pytest.fixture
def pool(monkeypatch):
    made = []
    class Worker:
        def __init__(self, binary, cancel=None):
            self.active = False
            self.ready = False
            self.closed = False
            self.proc = SimpleNamespace(poll=lambda: 0 if self.closed else None)
            made.append(self)
        def initialize(self, cancel=None):
            self.ready = True
        def ask(self, payload, cancel=None):
            return payload['c']
        def close(self):
            self.closed = True
    monkeypatch.setattr(workers, '_WORKERS', {})
    monkeypatch.setattr(workers, 'Worker', Worker)
    yield made
    workers.close_all()


def test_reimport_at_the_same_path_gets_a_new_loaded_program(tmp_path, pool):
    binary = tmp_path / 'game.so'
    binary.write_bytes(b'first')
    assert workers.request(binary, {'c': 'one'}, generation='first') == 'one'
    assert workers.request(binary, {'c': 'two'}, generation='first') == 'two'
    assert len(pool) == 1
    binary.write_bytes(b'next')
    workers.request(binary, {'c': 'three'}, generation='next')
    assert len(pool) == 2
    binary.write_bytes(b'third')
    workers.request(binary, {'c': 'four'}, generation='third')
    assert len(pool) == 3 and pool[0].closed
    assert len(workers._WORKERS) == workers.MAX_WORKERS


def test_cancelled_lease_never_starts_a_worker(tmp_path, pool):
    binary = tmp_path / 'game.so'
    binary.touch()
    with pytest.raises(NetworkError, match='stopped'):
        workers.request(binary, {}, cancel=lambda: True)
    assert not pool


def test_cancelled_reply_stops_worker():
    worker = object.__new__(workers.Worker)
    worker.responses = queue.Queue()
    closed = []
    worker.close = lambda: closed.append(True)
    with pytest.raises(NetworkError, match='stopped'):
        worker._reply(10, lambda: True)
    assert closed


def test_eof_reports_the_bounded_worker_log():
    worker = object.__new__(workers.Worker)
    worker.responses = queue.Queue()
    worker.responses.put(None)
    worker.logs = ['decompiler exited']
    with pytest.raises(NetworkError, match='decompiler exited'):
        worker._reply(10)
