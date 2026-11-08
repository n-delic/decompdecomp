"""One bounded verification lane overlaps bulk comparisons; never detached publication."""
import threading
from concurrent.futures import ThreadPoolExecutor

from .. import compiler


class Queue:
    def __init__(self, net, log):
        self.net, self.log = net, log
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='compiler-apply')
        self.pending = []
        self.active = None
        self.latest = {}
        self.lock = threading.Lock()

    def __call__(self, line):return self.log(line)

    def __getattr__(self, name):return getattr(self.log, name)

    def progress(self, value):
        with self.lock:
            self.latest = dict(value)
            payload = {**value, 'applying_function':self.active,
                       'apply_pending':sum(not f.done() for f in self.pending)}
            getattr(self.log, 'progress', lambda value:None)(payload)

    def submit(self, name, work):
        # Two outstanding results at most: one running and one waiting.
        if len(self.pending) >= 2:self.pending.pop(0).result()
        check = getattr(self.log, 'check_cancel', lambda:None)
        check()
        def run():
            check()
            with self.lock:
                self.active = name
                getattr(self.log, 'progress', lambda value:None)({**self.latest, 'applying_function':name, 'apply_pending':len(self.pending)})
            try:
                with self.net.progress.view(), compiler.cancellable(check):
                    check()
                    return work()
            finally:
                with self.lock:
                    self.active = None
                    payload = {**self.latest, 'applying_function':None,
                               'apply_pending':sum(not f.done() for f in self.pending)}
                    getattr(self.log, 'progress', lambda value:None)(payload)
        self.pending.append(self.pool.submit(run))

    def finish(self):
        if any(not f.done() for f in self.pending):
            self.progress({**self.latest, 'phase':'applying', 'function':self.active or 'queued results',
                           'stage':'Finishing queued rechecks'})
        for future in self.pending:future.result()
        self.pending.clear()

    def close(self):
        for future in self.pending:future.cancel()
        self.pool.shutdown(wait=True, cancel_futures=True)
