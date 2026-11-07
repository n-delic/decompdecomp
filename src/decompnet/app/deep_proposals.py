"""Bounded shared proposal batches, generated outside the app interpreter in maximum mode."""
import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

from .. import limits
from ..hostenv import NO_WINDOW, stop_process_tree
from ..network import NetworkError

MAX_CACHE = 32*1024*1024


class Worker:
    def __init__(self):
        self.process = subprocess.Popen(limits.capped([sys.executable,str(Path(__file__).with_name('deep_proposal_worker.py'))],memory=2<<30),
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=NO_WINDOW,
            start_new_session=os.name!='nt')
        self.lines = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self.read,daemon=True,name='deep-proposal-output')
        self.reader.start()

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

    def run(self, request, check):
        try:
            check()
            self.process.stdin.write(request+b'\n');self.process.stdin.flush()
            while True:
                check()
                try:line = self.lines.get(timeout=.05);break
                except queue.Empty:pass
            if line is None:raise NetworkError('Local proposal generator stopped. Start the deep search again.')
            result = json.loads(line)
            if 'error' in result:raise NetworkError('Local proposal generation failed: '+result['error'])
            return result
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed.is_set():return
        self.closed.set()
        if os.name!='nt' and self.process.poll() is None:
            try:os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
        stop_process_tree(self.process)
        self.reader.join(timeout=2)
        for stream in (self.process.stdin,self.process.stdout):
            try:stream.close()
            except OSError:pass


class Pool:
    def __init__(self, count):
        self.executor = ThreadPoolExecutor(count,thread_name_prefix='deep-proposals')
        self.capacity = threading.BoundedSemaphore(count*2)
        self.local = threading.local()
        self.workers = []
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.sizes = {}
        self.stop = threading.Event()
        self.submitted = 0
        self.running = 0

    def work(self, request, check):
        with self.lock:self.running += 1
        try:
            worker = getattr(self.local,'worker',None)
            if worker is None or worker.closed.is_set():
                worker = self.local.worker = Worker()
                with self.lock:
                    self.workers = [w for w in self.workers if not w.closed.is_set()]+[worker]
            def checkpoint():
                if self.stop.is_set():raise RuntimeError('Proposal generation stopped')
                check()
            return worker.run(request,checkpoint)
        finally:
            with self.lock:self.running -= 1

    def generate(self, args, kwargs, check):
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
                                size=sum(len(c.encode()) for c in result['choices'])+sum(len(c.encode())+len(f.encode()) for c,f in result['labels'].items())
                                self.sizes[key]=size
                                while len(self.sizes)>16 or sum(self.sizes.values())>MAX_CACHE:
                                    old=next(iter(self.sizes));self.sizes.pop(old);self.cache.pop(old,None)
                        future.add_done_callback(finished)
                    else:self.capacity.release()
            except BaseException:
                self.capacity.release()
                raise
        started=time.monotonic()
        while True:
            check()
            try:result = future.result(timeout=.05);break
            except TimeoutError:pass
        return list(result['choices']),dict(result['labels']),time.monotonic()-started

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True,cancel_futures=True)
        for worker in self.workers:worker.close()
