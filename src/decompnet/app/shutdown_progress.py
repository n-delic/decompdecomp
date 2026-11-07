"""Flushed terminal shutdown status, independent of a busy database or closed window."""
import sys
import threading
import time

SHUTDOWN_SECONDS = 60
RUNTIME_STOP_SECONDS = 5


def terminal_note(message, logger, *, prefix='shutdown', stream=None):
    line=f'[{prefix}] {message}'
    try:
        print(line,file=stream if stream is not None else sys.stderr,flush=True)
    except (OSError,ValueError):
        pass  # A closed/redirected terminal must not interrupt shutdown or final saves.
    try:logger.info(line)
    except (OSError,ValueError):pass


class Progress:
    def __init__(self, logger, *, interval=5, stream=None, clock=time.monotonic):
        self.logger=logger
        self.interval=interval
        self.stream=stream
        self.clock=clock
        self.stop=threading.Event()
        self.lock=threading.RLock()
        self.phase=None
        self.thread=None

    def note(self, message):
        terminal_note(message,self.logger,stream=self.stream)

    def update(self, label):
        with self.lock:
            self.phase=(label,self.clock())
            self.note(label+'...')

    def step(self, label, action):
        self.update(label)
        started=self.clock()
        try:result=action()
        except BaseException:
            self.note(f'Failed while {label.lower()}; see the app log for details.')
            raise
        with self.lock:
            self.phase=None
            self.note(f'{label} complete ({self.clock()-started:.1f}s).')
        return result

    def bounded_step(self, label, action, timeout):
        """Never wait indefinitely on cleanup; SQLite retains committed work for restart recovery."""
        self.update(label)
        started=self.clock()
        active=threading.Event();active.set()
        errors=[]
        def notify(message):
            if active.is_set():self.update(message)
        def work():
            try:action(notify)
            except BaseException as error:errors.append(error)
        worker=threading.Thread(target=work,daemon=True,name='shutdown-'+label.lower().replace(' ','-'))
        worker.start()
        worker.join(max(0,timeout))
        active.clear()
        with self.lock:self.phase=None
        if worker.is_alive():
            self.note(f'Timed out while {label.lower()} after {self.clock()-started:.1f}s. '
                      'Continuing shutdown; committed database work is kept and unfinished recovery resumes on startup.')
            return False
        if errors:
            self.note(f'Could not finish {label.lower()}: {errors[0]}. Committed work is kept; recovery will retry on startup.')
            return False
        self.note(f'{label} complete ({self.clock()-started:.1f}s).')
        return True

    def waiting(self):
        with self.lock:
            if self.phase:
                label,started=self.phase
                self.note(f'Still waiting: {label.lower()} ({self.clock()-started:.0f}s elapsed).')

    def _watch(self):
        while not self.stop.wait(self.interval):self.waiting()

    def __enter__(self):
        self.thread=threading.Thread(target=self._watch,daemon=True,name='shutdown-status')
        self.thread.start()
        return self

    def __exit__(self,*_):
        self.stop.set()
        self.thread.join(timeout=.2)
