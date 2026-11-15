"""A request lane stuck past its threshold saves the stacks of the threads holding it (lane_watch.py).

The fast lane's two threads once waited on a lock for over ten minutes: a project's quick map, its summary and the
sidebar hung while the same map built in 0.07 s on a copy, and no log said which lock held them."""
import asyncio
import threading

from decompnet import lane_watch, ui_diagnostics


class Clock:
    def __init__(self):
        self.now = 100.

    def __call__(self):
        return self.now


def _held_on_purpose(lock, entered):
    entered.set()
    with lock:
        pass


def test_a_stuck_lane_names_the_code_its_threads_wait_in(tmp_path):
    clock = Clock()
    watch = lane_watch.Watch(tmp_path / 'stacks.log', threshold=10, cooldown=60, clock=clock)
    lock, entered = threading.Lock(), threading.Event()
    lock.acquire()
    with watch.queued('fast', '/api/map') as running_key, watch.queued('fast', '/api/projects'):
        def worker():
            watch.running(running_key)
            _held_on_purpose(lock, entered)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        entered.wait(2)
        try:
            clock.now += 5
            assert watch.check() == []  # (under the threshold: nothing written)
            assert not (tmp_path / 'stacks.log').exists()
            clock.now += 6
            assert watch.check() == ['fast']
            report = (tmp_path / 'stacks.log').read_text()
            assert 'Request lane "fast" stuck' in report and '1 running, 1 waiting' in report
            assert '/api/map on thread' in report and '_held_on_purpose' in report
            assert 'waiting    11.0s  /api/projects' in report
            clock.now += 30
            assert watch.check() == []  # one report per lane per cooldown
            clock.now += 31
            assert watch.check() == ['fast']
            assert (tmp_path / 'stacks.log').read_text().count('Request lane "fast" stuck') == 2
        finally:
            lock.release()
            thread.join(2)
    assert watch.requests == {}  # finished requests leave the lane


def test_reports_stay_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(lane_watch, 'KEPT_BYTES', 400)
    clock = Clock()
    watch = lane_watch.Watch(tmp_path / 'stacks.log', threshold=1, cooldown=0, clock=clock)
    with watch.queued('ui', '/api/function'):
        for _ in range(20):
            clock.now += 2
            watch.check()
    assert len((tmp_path / 'stacks.log').read_text()) <= 400


def test_the_watch_thread_reports_without_being_asked(tmp_path):
    watch = lane_watch.Watch(tmp_path / 'stacks.log', threshold=.05, interval=.02)
    watch.start()
    try:
        with watch.queued('live', '/api/live'):
            for _ in range(100):
                if (tmp_path / 'stacks.log').exists():
                    break
                threading.Event().wait(.02)
    finally:
        watch.close()
    assert 'Request lane "live" stuck' in (tmp_path / 'stacks.log').read_text()


def test_the_diagnostics_middleware_names_the_api_path_without_its_query():
    seen = []

    async def app(scope, receive, send):
        seen.append(ui_diagnostics.current_route.get())

    async def serve(path):
        await ui_diagnostics.Diagnostics(app)({'type': 'http', 'path': path, 'query_string': b'slug=x'}, None, None)

    asyncio.run(serve('/api/map'))
    asyncio.run(serve('/index.html'))
    assert seen == ['/api/map', '']
