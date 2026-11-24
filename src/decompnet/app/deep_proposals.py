"""Bounded shared proposal batches, generated outside the app interpreter."""
import hashlib
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import weakref
from collections import OrderedDict, deque
from concurrent.futures import TimeoutError
from pathlib import Path

from .. import limits
from ..hostenv import NO_WINDOW, stop_process_tree
from ..network import NetworkError
from .purpose_work import Executor

MAX_CACHE = 32*1024*1024
RETRIES = 2  # a request whose worker died is asked again of a fresh one this many times before the pass fails
log = logging.getLogger('decompdecomp')


class WorkerStopped(NetworkError):
    """The generator process ended while a request was in flight (killed, crashed, replaced). Generation is pure, so
    the request may be asked again of a fresh worker."""


class Worker:
    def __init__(self):
        self.process = subprocess.Popen(limits.capped([sys.executable,str(Path(__file__).with_name('deep_proposal_worker.py'))],memory=2<<30),
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=NO_WINDOW,
            env={**os.environ,'DECOMP_PROPOSAL_WORKER':'1'},
            start_new_session=os.name!='nt')
        self.lines = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.errors = deque(maxlen=30)  # the worker's last words, for when it stops unexpectedly
        self.reader = threading.Thread(target=self.read,daemon=True,name='deep-proposal-output')
        self.reader.start()
        threading.Thread(target=self.drain,daemon=True,name='deep-proposal-errors').start()

    def drain(self):
        try:
            for line in self.process.stderr:
                self.errors.append(line.decode(errors='replace').rstrip())
        except (OSError,ValueError):pass

    def how_it_ended(self):
        """`exit status 1` / `killed by signal 9` with the worker's last stderr lines, for the log and the error."""
        try:code=self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:code=None
        if code is None:what='did not exit'
        elif code<0:what=f'killed by signal {-code}'
        else:what=f'exit status {code}'
        last=' | '.join(list(self.errors)[-5:])
        return what+(f'; last output: {last}' if last else '')

    def read(self):
        try:
            for line in self.process.stdout:
                while not self.closed.is_set():
                    try:self.lines.put(line,timeout=.05);break
                    except queue.Full:pass
        finally:
            while not self.closed.is_set():
                try:self.lines.put(None,timeout=.05);break
                except queue.Full:pass

    def run(self, request, check, handlers=None):
        write_errors = []
        self.input_lock = threading.Lock()

        def send():
            try:
                with self.input_lock:
                    self.process.stdin.write(request+b'\n')
                    self.process.stdin.flush()
            except (OSError, ValueError) as error:
                write_errors.append(error)

        def answer(question):
            """A question the child asked while generating (the project's symbols, original bytes): the reply goes down
            its stdin; a handler that fails answers None, so a heuristic skips what it can't read."""
            handler = (handlers or {}).get(question.get('op'))
            try:
                value = handler(*question.get('args', ())) if handler else None
            except Exception:  # noqa: BLE001 (a missing optional datum is not a failure of the generation)
                value = None
            with self.input_lock:
                self.process.stdin.write(json.dumps({'rpc_result': value}, ensure_ascii=False).encode() + b'\n')
                self.process.stdin.flush()

        # A large request can fill the pipe before a stuck child reads it.
        # Keep checking cancellation during both the send and the response.
        writer = threading.Thread(target=send, daemon=True, name='deep-proposal-input')
        try:
            check()
            writer.start()
            while True:
                check()
                if write_errors:
                    if isinstance(write_errors[0],(BrokenPipeError,ConnectionError)) and not self.closed.is_set():
                        how=self.how_it_ended()  # it ended before reading the request
                        log.warning('Proposal worker %s stopped before reading a request (%s)',self.process.pid,how)
                        raise WorkerStopped(f'Local proposal generator stopped ({how.split(";")[0]}). Retry the repair.')
                    raise write_errors[0]
                try:
                    line = self.lines.get(timeout=.05)
                except queue.Empty:
                    continue
                if line is not None and line.startswith(b'P'):
                    return line.strip()  # a pickled answer to an offloaded call (decompnet.app.offload)
                if line is not None and b'"rpc"' in line[:12] and 'rpc' in (question := json.loads(line)):
                    answer(question['rpc'])
                    continue
                break
            if line is None:
                if self.closed.is_set():raise NetworkError('Local proposal generator stopped. Retry the repair.')
                how=self.how_it_ended()
                log.warning('Proposal worker %s stopped during a request (%s)',self.process.pid,how)
                raise WorkerStopped(f'Local proposal generator stopped ({how.split(";")[0]}). Retry the repair.')
            result = json.loads(line)
            if 'error' in result:
                raise NetworkError('Local proposal generation failed: '+result['error'])
            from .repair_generation import report_cut_off
            report_cut_off(result.pop('cut_off',None) or ())
            return result
        except BaseException:
            self.close()
            raise
        finally:
            if writer.ident is not None:
                writer.join(timeout=2)

    def close(self):
        if self.closed.is_set():return
        self.closed.set()
        if os.name!='nt' and self.process.poll() is None:
            try:os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            except PermissionError:pass  # (macOS: it exited since poll, a zombie group; stop_process_tree follows)
        stop_process_tree(self.process)
        self.reader.join(timeout=2)
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr):
            try:stream.close()
            except OSError:pass


class Pool:
    def __init__(self, count, *, background=False):
        self.count=count
        self.background=background
        self.executor = Executor(max_workers=count, purpose='Repair proposal generation',thread_name_prefix='deep-proposals')
        self.capacity = threading.BoundedSemaphore(count*2)
        self.idle = []  # open workers no request holds: a worker belongs to the pool, not to a thread
        self.workers = []
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.sizes = {}
        self.stop = threading.Event()
        self.measured = weakref.WeakSet()
        self.submitted = 0
        self.running = 0

    def work(self, request, check):
        with self.lock:self.running += 1
        try:
            def checkpoint():
                from .purpose_work import checkpoint as check_owner
                check_owner()
                if self.stop.is_set():raise RuntimeError('Proposal generation stopped')
                check()
            for attempt in range(RETRIES+1):
                # A worker per *thread* leaked: the executor lends its threads back after a few idle seconds and
                # borrows others, so every new thread started a process (~50 MB each) and the old ones stayed up
                # until the pool closed: hundreds of them, tens of GB, in half an hour of a deep search.
                with self.lock:
                    worker = None
                    while self.idle and worker is None:
                        worker = self.idle.pop()
                        if worker.closed.is_set():worker = None
                if worker is None:
                    worker = Worker()
                    with self.lock:
                        self.workers = [w for w in self.workers if not w.closed.is_set()]+[worker]
                try:
                    result = worker.run(request,checkpoint)
                except WorkerStopped:  # (run closed it: the next try starts a fresh one)
                    if attempt == RETRIES:raise
                    log.info('Asking a fresh proposal worker again (attempt %d of %d)',attempt+2,RETRIES+1)
                    continue
                with self.lock:
                    if not self.stop.is_set() and not worker.closed.is_set():self.idle.append(worker);return result
                worker.close()
                return result
        finally:
            with self.lock:self.running -= 1

    def submit(self, args, kwargs, check):
        if self.background:kwargs = {**kwargs, '_background':True}
        request = json.dumps([args,kwargs],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
        key = hashlib.sha256(request).digest()
        with self.lock:future = self.cache.get(key)
        if future is None:
            while not self.capacity.acquire(timeout=.05):check()
            try:
                with self.lock:
                    future = self.cache.get(key)
                    if future is None:
                        if self.stop.is_set():raise RuntimeError('Proposal pool stopped')
                        future = self.executor.submit(self.work,request,check)
                        self.submitted += 1
                        self.cache[key] = future
                        def finished(value):
                            self.capacity.release()
                            with self.lock:
                                if value.cancelled() or value.exception() is not None:
                                    self.cache.pop(key,None);return
                                result=value.result()
                                if 'candidates' in result:
                                    records=result.pop('candidates')
                                    result['choices']=[c for c,_ in records]
                                    result['labels']={c:f for c,f in records if f}
                                size=sum(len(c.encode()) for c in result['choices'])+sum(len(c.encode())+len(f.encode()) for c,f in result['labels'].items())
                                self.sizes[key]=size
                                while len(self.sizes)>16 or sum(self.sizes.values())>MAX_CACHE:
                                    old=next(iter(self.sizes));self.sizes.pop(old);self.cache.pop(old,None)
                        future.add_done_callback(finished)
                    else:self.capacity.release()
            except BaseException:
                self.capacity.release()
                raise
        return future

    def result(self,future,check):
        started=time.monotonic()
        while True:
            check()
            try:result = future.result(timeout=.05);break
            except TimeoutError:pass
        with self.lock:
            fresh = future not in self.measured
            self.measured.add(future)
        if fresh:
            from .. import compile_timing
            for name, value in result.get('timings', {}).items():
                if name in {'proposal_generation', 'cleanup_generation'} and isinstance(value,(int,float)) and value >= 0:
                    compile_timing.add(name,value)
        return list(result['choices']),dict(result['labels']),time.monotonic()-started

    def generate(self,args,kwargs,check):
        return self.result(self.submit(args,kwargs,check),check)

    def stream(self,args,kwargs,check):
        # Split pure generation by stage identity; identical lanes share each shard.
        # Arrival order changes exploration order, never compiler/judge authority.
        started=time.monotonic()
        count=min(4,self.count)
        pending=[self.submit(args,{**kwargs,'_partition':(index,count)},check) for index in range(count)]
        while pending:
            check()
            ready=next((future for future in pending if future.done()),None)
            if ready is None:
                try:pending[0].result(timeout=.05)
                except TimeoutError:pass
                continue
            pending.remove(ready)
            choices,labels,_=self.result(ready,check)
            yield choices,labels,time.monotonic()-started
            started=time.monotonic()

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True,cancel_futures=True)
        for worker in self.workers:worker.close()
