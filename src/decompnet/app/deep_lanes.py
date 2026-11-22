"""A deep pass's function turns in child processes, each with its own engine and its own interpreter.

One interpreter judged every candidate of a whole deep pass: about 9.5 ms of Python each, so a pass topped out near
a hundred candidates a second however many compile slots or threads it had, with most of the machine idle. Each
lane here is a child process that opens the same project database (like the permuter worker) and runs the ordinary
`deep_permuter.run` for one function at a time: the same search, judge, linker and history, in parallel.

The pass keeps everything else: which function comes next, its records and progress, Stop. Over a private pipe the
child sends its log lines, progress, compile counters and the result; the parent sends the turn and, when asked,
a cancel. A child that can't start or stops makes the pass run that turn in the app as before.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..hostenv import NO_WINDOW
from ..network import NetworkError

log = logging.getLogger('decompdecomp')
METRIC_SECONDS = .5  # how often a child reports its compile counters (the pass's live rates)
PROGRESS_SECONDS = .25
IDLE_SECONDS = 60  # a lane with no turn for this long is closed (Lanes.trim); one starts again on demand  # the most often a turn's progress crosses to the app
COUNTERS = ('considered', 'generated', 'invocations', 'requests', 'execute', 'queue', 'setup', 'generation',
            'proposal_generation', 'cleanup_generation', 'local_generation', 'link', 'history_write',
            'history_transactions', 'history_statements')


def enabled():
    """Off with DECOMP_DEEP_LANES=0: every turn then runs in the app's own interpreter, as before."""
    return os.environ.get('DECOMP_DEEP_LANES', '1') != '0'


class Unavailable(Exception):
    """The lane could not take the turn: the caller runs it in this process."""


class Lane:
    def __init__(self, net, *, background):
        home = str(net.workspace)
        log_path = Path(home) / '.decomp' / 'logs' / 'deep-lanes.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        argv = [sys.executable, '-m', 'decompnet.app.deep_lanes', '--child', net.store.path, home]
        if background:
            argv.append('--background')
        with log_path.open('ab') as errors:
            # Buffered: unbuffered, reading lines made a system call per byte, and that one reader thread used more
            # of the app's processor than everything else together on a pass with 288 search threads.
            self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                            creationflags=NO_WINDOW)
        self.messages = queue.Queue()
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True, name='deep-lane-output').start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.messages.put(None)

    def alive(self):
        return self.process.poll() is None

    def send(self, message):
        try:
            self.process.stdin.write((json.dumps(message) + '\n').encode())
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise Unavailable() from error

    def close(self):
        try:
            self.send({'action': 'shutdown'})
            self.process.stdin.close()
        except (Unavailable, OSError):
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


class Lanes:
    """Up to `count` children, started as turns need them; each runs one turn at a time."""

    def __init__(self, net, count, slots, *, maximum=False):
        self.net, self.count, self.maximum = net, max(1, count), maximum
        # Each lane's share of the machine: compiles mostly wait on the compiler, so about two in flight per core
        # keep every core busy. An equal slice of a large configured slot count (256 over 24 lanes) gave each lane
        # 10 compile threads and as many helper processes, hundreds in all on 18 cores, mostly idle.
        share = max(2, -(-2 * (os.cpu_count() or 1) // self.count))
        self.slots = max(2, min(256, share, slots // self.count if slots else share))
        self.idle = queue.Queue()
        self.started = 0
        self.lock = threading.Lock()
        self.failed = False
        self.ident = 0

    def _take(self, check):
        while True:
            try:
                lane = self.idle.get_nowait()
            except queue.Empty:
                lane = None
            if lane is not None:
                if lane.alive():
                    return lane
                with self.lock:
                    self.started -= 1
                continue
            with self.lock:
                start = not self.failed and self.started < self.count
                if start:
                    self.started += 1
            if start:
                try:
                    return Lane(self.net, background=not self.maximum)
                except OSError as error:
                    with self.lock:
                        self.started -= 1
                        self.failed = True
                    raise Unavailable() from error
            if self.failed:
                raise Unavailable()
            check()
            try:
                lane = self.idle.get(timeout=.1)
            except queue.Empty:
                continue
            if lane.alive():
                return lane
            with self.lock:
                self.started -= 1

    def search(self, slug, name, minutes, variants, function_log, **options):
        """deep_permuter.run(...) for one function in a lane; raises Unavailable when no lane can take it."""
        return self._request(function_log, action='search', slug=slug, name=name, minutes=minutes,
                             variants=variants, **options)

    def donors(self, slug, function_ids, function_log, *, imported_since=None):
        return self._request(function_log, action='donors', slug=slug, function_ids=function_ids,
                             imported_since=imported_since)

    def _request(self, function_log, **payload):
        function_log.check_cancel()
        if self.failed:
            raise Unavailable()
        lane = self._take(function_log.check_cancel)
        with self.lock:
            self.ident += 1
            ident = self.ident
        pool = getattr(function_log, 'compile_pool', None)
        metrics = getattr(pool, 'metrics', None)
        keep = False
        try:
            lane.send({'ident': ident, 'slots': self.slots, 'maximum': self.maximum, **payload})
            cancelled = None
            deadline = None
            while True:
                try:
                    message = lane.messages.get(timeout=.1)
                except queue.Empty:
                    message = False
                if message is None:
                    if cancelled is not None:
                        raise cancelled
                    raise Unavailable()
                if message is False:
                    if cancelled is None:
                        try:
                            function_log.check_cancel()
                        except BaseException as error:
                            cancelled = error
                            deadline = time.monotonic() + 30
                            lane.send({'action': 'cancel', 'ident': ident})
                    elif time.monotonic() > deadline:
                        raise cancelled  # the child is stuck: the lane is closed below
                    continue
                if message.get('ident') != ident:
                    continue
                kind = message.get('kind')
                if kind in ('line', 'progress'):
                    try:
                        if kind == 'line':
                            function_log(message['line'])
                        elif (progress := getattr(function_log, 'progress', None)) is not None:
                            progress(message['value'])
                    except BaseException as error:  # (a stop surfacing here: let the child end its turn)
                        if cancelled is None:
                            cancelled = error
                            deadline = time.monotonic() + 30
                            lane.send({'action': 'cancel', 'ident': ident})
                elif kind == 'metrics':
                    if metrics is not None:
                        for key, value in message['values'].items():
                            metrics.add(key, value)
                        metrics.set_remote(ident, message.get('live'))
                        if refresh := getattr(function_log, 'refresh_metrics', None):
                            refresh()
                elif kind == 'reply':
                    keep = True
                    if cancelled is not None:
                        raise cancelled
                    if 'error' in message:
                        if message.get('type') == 'NetworkError':
                            raise NetworkError(message['error'])
                        raise RuntimeError(message['error'])
                    return message['result']
        finally:
            if metrics is not None:
                metrics.set_remote(ident, None)
                if refresh := getattr(function_log, 'refresh_metrics', None):
                    refresh()
            if keep and lane.alive():
                lane.idle_since = time.monotonic()
                self.idle.put(lane)
            else:
                with self.lock:
                    self.started -= 1
                lane.close()

    def trim(self, idle_seconds=IDLE_SECONDS):
        """Close the lanes that have waited for a turn longer than `idle_seconds`, with their proposal workers.

        A looping pass that waits for scored C or host claims, or runs fewer turns than it has lanes near its end, kept
        every idle lane alive, each with its proposal worker processes: about 70 idle processes in one app. A closed
        lane is started again when a turn needs it."""
        now = time.monotonic()
        kept = []
        while True:
            try:
                lane = self.idle.get_nowait()
            except queue.Empty:
                break
            if lane.alive() and now - getattr(lane, 'idle_since', now) < idle_seconds:
                kept.append(lane)
                continue
            with self.lock:
                self.started -= 1
            lane.close()
        for lane in kept:
            self.idle.put(lane)

    def close(self):
        self.trim(0)


class _Cancelled(Exception):
    pass


def child(db_path, workspace, background):
    """Own one engine; run the turns the pass sends, one at a time. stdout carries only JSON lines."""
    from . import deep_compilation, deep_permuter
    from .deep_sweep import Sweeps
    from .engine import RomNetwork
    if background:
        from .workers import started_priority
        started_priority()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    out = sys.stdout
    sys.stdout = sys.stderr  # nothing printed may corrupt the messages
    net = RomNetwork(db_path, Path(workspace))
    net.deep_sweeps = Sweeps(net, None)  # (recording only: the pass itself stays in the app)
    net.progress.passive = True  # the app keeps the saved tree in sync (Progress.sync)
    output = threading.Lock()
    pools = {}
    cancels = {}

    def emit(message):
        data = json.dumps(message, default=str) + '\n'
        with output:
            out.write(data)
            out.flush()

    def pool_for(slots, maximum):
        if (slots, maximum) not in pools:
            # In-thread compiles: this process is a lane of its own, so a child per compile thread
            # (compile_worker.py, for the app's one interpreter) would only add a process and a round trip.
            pools[(slots, maximum)] = deep_compilation.Pool(slots, maximum=maximum, remote=False)
        return pools[(slots, maximum)]

    def run(message):
        from .deep_throughput import LIVE_WORKER_COUNTERS, RANK_COUNTERS
        ident = message['ident']
        stop = cancels[ident]
        pool = pool_for(message['slots'], message['maximum'])
        sent = pool.metrics.counts()
        finished = threading.Event()
        latest = {'sent': 0.0, 'value': None}

        def last_progress():
            with output:
                value, latest['value'] = latest['value'], None
            if value is not None:
                emit({'kind': 'progress', 'ident': ident, 'value': value})

        def counters():
            nonlocal sent
            current = pool.metrics.counts()
            delta = {key: current.get(key, 0) - sent.get(key, 0) for key in (*COUNTERS, *RANK_COUNTERS)
                     if current.get(key, 0) != sent.get(key, 0)}
            live = {key: current.get(key, 0) for key in LIVE_WORKER_COUNTERS}
            live_changed = any(value != sent.get(key, 0) for key, value in live.items())
            sent = current
            if delta or live_changed:
                emit({'kind': 'metrics', 'ident': ident, 'values': delta, 'live': live})

        def report():
            while not finished.wait(METRIC_SECONDS):
                counters()
        reporter = threading.Thread(target=report, daemon=True, name='deep-lane-metrics')
        reporter.start()

        class Log:
            compile_pool = pool

            def __call__(self, line):
                emit({'kind': 'line', 'ident': ident, 'line': str(line)})

            def progress(self, value):
                self.check_cancel()  # (as the app's job log does: searches report often, so they stop promptly)
                # At most a few a second: a search reports after nearly every candidate, and every report crosses
                # the pipe and the app's interpreter. The last one is always sent before the reply.
                now = time.monotonic()
                with output:
                    latest['value'] = value
                    if now - latest['sent'] < PROGRESS_SECONDS:
                        return
                    latest['sent'], latest['value'] = now, None
                emit({'kind': 'progress', 'ident': ident, 'value': value})

            def check_cancel(self):
                if stop.is_set():
                    raise _Cancelled('Deep pass stopped')
        try:
            options = {key: message[key] for key in ('compiler_profiles', 'threads_per_function', 'expanded_budget', 'rank_candidates') if key in message}
            result = deep_permuter.run(net, message['slug'], message['name'], message['minutes'], message['variants'],
                                       Log(), **options)
            finished.set()
            reporter.join()
            counters()
            last_progress()
            emit({'kind': 'reply', 'ident': ident, 'result': result})
        except BaseException as error:
            finished.set()
            reporter.join()
            counters()
            last_progress()
            if not isinstance(error, (_Cancelled, NetworkError)):
                log.exception('Deep lane turn failed')
            emit({'kind': 'reply', 'ident': ident, 'error': str(error) or type(error).__name__,
                  'type': 'NetworkError' if isinstance(error, NetworkError) else type(error).__name__})
            if not isinstance(error, Exception):
                raise
        finally:
            cancels.pop(ident, None)

    def run_donors(message):
        from .similar_phase import search_partition
        ident = message['ident']
        stop = cancels[ident]
        try:
            def report(value):
                emit({'kind': 'progress', 'ident': ident, 'value': value})
            result = search_partition(net, message['slug'], message['function_ids'], stop, report,
                                      imported_since=message.get('imported_since'))
            emit({'kind': 'reply', 'ident': ident, 'result': result})
        except Exception as error:
            log.exception('Cross-game search lane failed')
            emit({'kind': 'reply', 'ident': ident, 'error': str(error),
                  'type': 'NetworkError' if isinstance(error, NetworkError) else type(error).__name__})
        finally:
            cancels.pop(ident, None)

    requests = queue.Queue()

    def read():
        for line in sys.stdin:
            message = json.loads(line)
            if message.get('action') == 'cancel':
                if stop := cancels.get(message.get('ident')):
                    stop.set()
            else:
                if message.get('action') in ('search', 'donors'):
                    cancels[message['ident']] = threading.Event()
                requests.put(message)
        for stop in list(cancels.values()):
            stop.set()
        requests.put(None)
    threading.Thread(target=read, daemon=True, name='deep-lane-input').start()
    try:
        while (message := requests.get()) is not None and message.get('action') != 'shutdown':
            if message.get('action') == 'search':
                run(message)
            elif message.get('action') == 'donors':
                run_donors(message)
    finally:
        for stop in list(cancels.values()):
            stop.set()
        for pool in pools.values():
            pool.close()
        os._exit(0)  # (daemon threads and compiler sessions end with the process)


if __name__ == '__main__' and len(sys.argv) >= 4 and sys.argv[1] == '--child':
    child(sys.argv[2], sys.argv[3], '--background' in sys.argv[4:])
