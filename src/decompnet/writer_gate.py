"""Cross-process writer admission, outside SQLite and the process-local connection mutex.

The OS releases a crashed worker's advisory lock. Never unlink the sidecar: another
process may already have it open. Readers do not acquire this lock.
"""
import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_ACTIVE=threading.local()


class WriterGate:
    def __init__(self,path):
        self.path=None if str(path)==':memory:' else Path(str(Path(path).resolve())+'.writer.lock')
        self.local=threading.local()
        self.memory=threading.RLock()

    @contextmanager
    def hold(self,*,blocking=True,wait=None):
        if getattr(self.local,'depth',0):
            self.local.depth+=1
            try:yield True
            finally:self.local.depth-=1
            return
        held=getattr(_ACTIVE,'paths',set())
        if self.path is not None and self.path in held:
            if not blocking:
                yield False
                return
            raise RuntimeError('A second connection cannot recursively write the same database.')
        fd=None;acquired=False
        try:
            if self.path is not None:
                fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600)
            while not acquired:
                try:
                    if fd is None:acquired=self.memory.acquire(blocking=False)
                    elif os.name=='nt':
                        import msvcrt
                        os.lseek(fd,0,os.SEEK_SET)
                        # CRT region locks may extend past EOF; no racy initialization write.
                        msvcrt.locking(fd,msvcrt.LK_NBLCK,1);acquired=True
                    else:
                        import fcntl
                        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);acquired=True
                except OSError as error:
                    busy=(errno.EACCES,errno.EAGAIN,errno.EDEADLK) if os.name=='nt' else (errno.EAGAIN,errno.EWOULDBLOCK)
                    if error.errno not in busy:raise
                if not acquired:
                    if not blocking:
                        yield False
                        return
                    if wait is not None:wait()
                    time.sleep(.01)
            self.local.depth=1
            if self.path is not None:
                _ACTIVE.paths=held
                held.add(self.path)
            try:yield True
            finally:
                self.local.depth=0
                if self.path is not None:held.remove(self.path)
        finally:
            try:
                if acquired:
                    if fd is None:self.memory.release()
                    elif os.name=='nt':
                        import msvcrt
                        os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(fd,fcntl.LOCK_UN)
            finally:
                if fd is not None:os.close(fd)
