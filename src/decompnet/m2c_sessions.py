"""One bounded, warmed draft generator per explicit autopilot worker."""
import json
import os
import queue
import subprocess
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from . import compiler, limits
from .hostenv import NO_WINDOW, stop_process_tree

_current=ContextVar('m2c_session',default=None)


class Session:
    def __init__(self, enabled):
        self.enabled=enabled
        self.owner=threading.get_ident()
        self.process=None
        self.key=None
        self.requests=0
        self.reader=None

    def close(self):
        if self.process is None:return
        stop_process_tree(self.process)
        self.reader.join(timeout=2)
        for stream in (self.process.stdin,self.process.stdout):stream.close()
        self.process=None

    def run(self, argv, timeout):
        executable=Path(argv[0])
        python=executable.with_name('python.exe' if os.name=='nt' else 'python')
        key=(str(executable),executable.stat().st_mtime_ns,executable.stat().st_size)
        if self.process is not None and (self.key!=key or self.requests>=64):self.close()
        if self.process is None:
            self.lines=queue.Queue()
            self.process=subprocess.Popen(limits.capped([str(python),str(Path(__file__).with_name('m2c_worker.py'))],memory=2<<30),
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=NO_WINDOW)
            process=self.process
            def read():
                try:
                    for line in process.stdout:self.lines.put(line)
                finally:self.lines.put(None)
            self.reader=threading.Thread(target=read,name='autopilot-m2c-output',daemon=True)
            self.reader.start();self.key=key;self.requests=0
        try:
            self.process.stdin.write(json.dumps(argv[1:]).encode()+b'\n');self.process.stdin.flush()
            started=time.monotonic()
            while True:
                if check:=compiler._CANCEL_CHECK.get():check()
                if time.monotonic()-started>=timeout:raise subprocess.TimeoutExpired(argv,timeout)
                try:line=self.lines.get(timeout=.05);break
                except queue.Empty:pass
            if line is None:raise OSError('Draft generator stopped')
            data=json.loads(line);self.requests+=1
            return subprocess.CompletedProcess(argv,data['returncode'],data['stdout'],data['stderr'])
        except BaseException:
            self.close()
            raise


def run(argv, timeout):
    session=_current.get()
    if session is None or session.owner!=threading.get_ident() or not session.enabled.is_set():return None
    return session.run(argv,timeout)


@contextmanager
def scope(enabled):
    if _current.get() is not None:
        yield;return
    session=Session(enabled);token=_current.set(session)
    try:yield
    finally:
        _current.reset(token);session.close()
