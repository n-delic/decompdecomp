"""Request timings are opt-in, isolated and omit bindings/source."""
import time
from concurrent.futures import ThreadPoolExecutor

from starlette.responses import JSONResponse

from decompnet import request_timing
from decompnet.store import Store


def test_timings_capture_sql_and_encoding_and_reset_after_error(tmp_path, monkeypatch):
    store=Store(tmp_path/'timing.db')
    def work():
        return dict(store.one('SELECT 1 AS n'))
    assert 'Server-Timing' not in request_timing.response(work,time.monotonic(),JSONResponse,'/api/function').headers
    monkeypatch.setenv('DECOMP_PERF','1')
    def run(n):
        def query():
            for _ in range(n):
                work()
            assert request_timing._current.get()['queries'] == n
            return {'n':n}
        return request_timing.response(query,time.monotonic()-.01,JSONResponse,'/api/function')
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(run,[2,7]))
    assert all('sql;dur=' in r.headers['Server-Timing'] and 'encode;dur=' in r.headers['Server-Timing'] for r in results)
    def failed():
        raise ValueError('test')
    try:
        request_timing.response(failed,time.monotonic(),JSONResponse,'/api/function')
    except ValueError:
        pass
    assert request_timing._current.get() is None
    store.conn.close()


def test_timings_expose_connection_lock_execute_and_fetch(tmp_path, monkeypatch):
    store = Store(tmp_path / 'phases.db')
    monkeypatch.setenv('DECOMP_PERF', '1')
    response = request_timing.response(lambda: dict(store.one('SELECT 1 AS n')), time.monotonic(), JSONResponse, '/api/test')
    header = response.headers['Server-Timing']
    assert all(name + ';dur=' in header for name in ('sql_connection', 'sql_lock_wait', 'sql_execute', 'sql_fetch'))
    phases = store._local.read_phases
    assert all(phases[name] >= 0 for name in ('connection', 'execute', 'fetch'))
