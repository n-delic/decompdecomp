"""Private reusable compiler workspaces for saved verification, independent of background limits."""
import queue
import threading
from concurrent.futures import Future

from .. import compile_sessions, compiler


def width():
    return min(4,compiler.concurrency_limit())


class Pool:
    def __init__(self,count):
        self.queue=queue.Queue(maxsize=count)
        self.threads=[]
        for index in range(count):
            thread=threading.Thread(target=self.work,name=f'saved-compile-{index}',daemon=True)
            thread.start();self.threads.append(thread)

    def work(self):
        # Each thread owns and eventually closes its own private staging directory.
        # Compiler/header bytes are still fingerprinted before every invocation.
        with compile_sessions.session():
            while (item:=self.queue.get()) is not None:
                future,fn,args=item
                if not future.set_running_or_notify_cancel():continue
                try:future.set_result(fn(*args))
                except BaseException as error:future.set_exception(error)

    def submit(self,fn,*args):
        future=Future()
        self.queue.put((future,fn,args))
        return future

    def shutdown(self,wait=False,cancel_futures=True):
        if cancel_futures:
            while True:
                try:item=self.queue.get_nowait()
                except queue.Empty:break
                if item is not None:item[0].cancel()
        for _ in self.threads:self.queue.put(None)
        if wait:
            for thread in self.threads:thread.join()
