"""Stagger optional startup tasks; expose each task and its cooperative stop control."""
import queue
import threading
import time
from dataclasses import dataclass, field

from . import maintenance


@dataclass
class Task:
    key: str
    label: str
    fn: object
    project: str = ''
    with_log: bool = False
    state: str = 'queued'
    detail: str = 'Waiting for the startup maintenance slot'
    stop: threading.Event = field(default_factory=threading.Event)
    thread_id: int | None = None


class StartupQueue:
    def __init__(self, logger, clock=time.monotonic, delay=7, spacing=2, quiet=.4, max_defer=3):
        self.logger, self.clock = logger, clock
        self.delay, self.spacing, self.quiet, self.max_defer = delay, spacing, quiet, max_defer
        self.stop = threading.Event()
        self.tasks = queue.Queue()
        self.records = {}
        self.lock = threading.Lock()
        self.last_ui = float('-inf')
        self.wait = self.stop.wait

    def touch(self):
        self.last_ui = self.clock()
        maintenance.touch()

    def add(self, label, fn, *, project='', with_log=False):
        with self.lock:
            key = str(len(self.records)+1)
            task = self.records[key] = Task(key,label,fn,project,with_log)
        self.tasks.put(task)
        return key

    def snapshot(self):
        with self.lock:
            return [dict(key=t.key,title=t.label,project=t.project,state=t.state,detail=t.detail,
                         thread_id=t.thread_id) for t in self.records.values()
                    if t.state in {'queued','waiting','running','stopping'}]

    def cancel(self, key):
        with self.lock:
            task = self.records.get(key)
            if task is None or task.state not in {'queued','waiting','running','stopping'}:
                return False
            task.stop.set()
            task.state = 'stopping' if task.thread_id else 'stopped'
            task.detail = 'Stop requested; finishing the current safe step'
            return True

    def ready(self, earliest):
        deadline = earliest + self.max_defer
        while not self.stop.is_set():
            now = self.clock()
            if now >= earliest and (now-self.last_ui >= self.quiet or now >= deadline):
                return True
            self.wait(min(.1, max(.001, max(earliest, min(deadline, self.last_ui+self.quiet))-now)))
        return False

    def _execute(self, task):
        from .workers import lower_priority
        lower_priority()
        def check_cancel():
            if task.stop.is_set():raise maintenance.Cancelled('Startup task stopped by you')
        def log(message):
            check_cancel()
            with self.lock:task.detail = str(message)
            self.logger.info('%s: %s', task.label, message)
        def progress(value):
            check_cancel()
            detail = value.get('phase','Working')
            if value.get('total'):detail += f" · {value.get('done',0)} / {value['total']}"
            if value.get('game'):detail += f" · {value['game']}"
            if value.get('function'):detail += f" · {value['function']}"
            with self.lock:task.detail = detail
        log.check_cancel, log.progress = check_cancel, progress
        with self.lock:
            task.thread_id = threading.get_native_id()
            task.state = 'waiting'
        try:
            with maintenance.slot(task.stop) as ready:
                if not ready:raise maintenance.Cancelled('Startup task stopped before starting')
                with maintenance.cancellation(task.stop):
                    check_cancel()
                    with self.lock:
                        task.state, task.detail = 'running', 'Starting; stop takes effect at the next safe checkpoint'
                    maintenance.checkpoint()
                    task.fn(log) if task.with_log else task.fn()
                    check_cancel()
            with self.lock:task.state = 'done'
        except maintenance.Cancelled:
            with self.lock:task.state, task.detail = 'stopped', 'Stopped by you; completed work is retained'
        except Exception:
            with self.lock:task.state, task.detail = 'error', 'Startup task failed; see the app log'
            self.logger.exception('Startup task failed: %s', task.label)

    def run(self):
        earliest = self.clock() + self.delay
        while not self.stop.is_set():
            try:task = self.tasks.get_nowait()
            except queue.Empty:return
            if task.stop.is_set():continue
            if not self.ready(earliest):return
            if task.stop.is_set():continue
            # A task owns its own thread for attribution, but no second startup task
            # starts until it finishes. More threads would increase CPU contention.
            worker = threading.Thread(target=self._execute,args=(task,),daemon=True,name=f'startup-task-{task.key}')
            worker.start()
            while worker.is_alive():worker.join(.1)
            earliest = self.clock() + self.spacing

    def start(self):
        # Optional work can be added after startup drained. Never create a second
        # coordinator when a restore queues maintenance while the first is active.
        with self.lock:
            if getattr(self,'_runner',None) is not None:return
            def drain():
                try:self.run()
                finally:
                    with self.lock:
                        self._runner=None
                        again=not self.stop.is_set() and not self.tasks.empty()
                    if again:self.start()
            self._runner=threading.Thread(target=drain,daemon=True,name='startup-maintenance')
            self._runner.start()

    def close(self):
        self.stop.set()
        for key in list(self.records):self.cancel(key)
