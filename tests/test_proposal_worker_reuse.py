"""A deep search's proposal workers belong to the pool, not to the threads the executor lends it."""
import threading

from decompnet.app import deep_proposals


class FakeWorker:
    made = []

    def __init__(self):
        self.closed = threading.Event()
        FakeWorker.made.append(self)

    def run(self, request, check):
        return {'choices': [], 'labels': {}}

    def close(self):
        self.closed.set()


def test_requests_from_many_threads_share_the_pools_workers(monkeypatch):
    FakeWorker.made = []
    monkeypatch.setattr(deep_proposals, 'Worker', FakeWorker)
    pool = deep_proposals.Pool(2)
    try:
        for _ in range(20):  # each on a thread of its own, as the executor's lent threads come and go
            thread = threading.Thread(target=pool.work, args=(b'{}', lambda: None))
            thread.start()
            thread.join()
        assert len(FakeWorker.made) == 1
    finally:
        pool.close()
    assert all(worker.closed.is_set() for worker in FakeWorker.made)
