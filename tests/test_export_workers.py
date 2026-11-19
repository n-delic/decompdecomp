"""Export workers overlap independent checks and keep result order bounded."""
from threading import Event

import pytest

from decompnet.rom import export_workers


def test_export_checks_overlap_and_keep_result_order(monkeypatch):
    monkeypatch.setattr(export_workers.os, 'cpu_count', lambda: 4)
    monkeypatch.setattr(export_workers.compiler, 'concurrency_limit', lambda: 2)
    second_started = Event()

    def compile_one(index):
        if index == 0:
            assert second_started.wait(3)
        if index == 1:
            second_started.set()
        return index

    with export_workers.ordered(compile_one, range(8)) as results:
        assert list(results) == list(range(8))


def test_stopping_an_export_does_not_drain_its_input(monkeypatch):
    monkeypatch.setattr(export_workers.os, 'cpu_count', lambda: 4)
    monkeypatch.setattr(export_workers.compiler, 'concurrency_limit', lambda: 2)
    submitted = []

    def items():
        for index in range(100):
            submitted.append(index)
            yield index

    def stop():
        raise RuntimeError('Stopped')

    with pytest.raises(RuntimeError, match='Stopped'), export_workers.ordered(int, items(), stop) as results:
        next(results)
    assert submitted == [0, 1]
