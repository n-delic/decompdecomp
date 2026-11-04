"""Stagger optional startup scans, yielding briefly to foreground project navigation."""
import queue
import threading
import time


class StartupQueue:
    def __init__(self, logger, clock=time.monotonic, delay=7, spacing=2, quiet=.4, max_defer=3):
        self.logger, self.clock = logger, clock
        self.delay, self.spacing, self.quiet, self.max_defer = delay, spacing, quiet, max_defer
        self.stop = threading.Event()
        self.tasks = queue.Queue()
        self.last_ui = float('-inf')
        self.wait = self.stop.wait

    def touch(self):
        self.last_ui = self.clock()

    def add(self, label, fn):
        self.tasks.put((label, fn))

    def ready(self, earliest):
        deadline = earliest + self.max_defer
        while not self.stop.is_set():
            now = self.clock()
            if now >= earliest and (now-self.last_ui >= self.quiet or now >= deadline):
                return True
            self.wait(min(.1, max(.001, max(earliest, min(deadline, self.last_ui+self.quiet))-now)))
        return False

    def run(self):
        earliest = self.clock() + self.delay
        while not self.stop.is_set():
            try:
                label, fn = self.tasks.get_nowait()
            except queue.Empty:
                return
            if not self.ready(earliest):
                return
            try:
                fn()
            except Exception:
                self.logger.exception('Startup task failed: %s', label)
            earliest = self.clock() + self.spacing

    def start(self):
        threading.Thread(target=self.run, daemon=True, name='startup-maintenance').start()

    def close(self):
        self.stop.set()
