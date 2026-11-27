"""Compiler admission shared by the app and its deep search lanes."""
import threading
from collections import deque
from concurrent.futures import Future, TimeoutError
from contextlib import contextmanager


class Admission:
    def __init__(self, default):
        self.default = default
        self.active = 0
        self.requests = {}
        self.condition = threading.Condition()
        self.foreground = deque()
        self.background = deque()

    @property
    def limit(self):
        return max([self.default, *self.requests.values()])

    def group(self, limit):
        return Group(self, limit)

    def request(self, group=None, *, background=False):
        """Reserve asynchronously so a lane's message reader can keep receiving releases."""
        ticket = Future()
        with self.condition:
            pending = self.background if background else self.foreground
            pending.append((ticket, group))
            ready = self._ready()
        for grant in ready:
            grant.set_result(None)
        return ticket

    def _ready(self):
        ready = []
        for pending in (self.foreground, self.background):
            for _ in range(len(pending)):
                ticket, group = pending[0]
                if ticket.cancelled():
                    pending.popleft()
                    continue
                if self.active >= self.limit:
                    break
                pending.popleft()
                if group is not None and group.active >= group.limit:
                    pending.append((ticket, group))
                    continue
                if ticket.set_running_or_notify_cancel():
                    self.active += 1
                    if group is not None:
                        group.active += 1
                    ready.append(ticket)
        return ready

    def acquire(self, timeout=None):
        ticket = self.request()
        try:
            ticket.result(timeout)
        except TimeoutError:
            if ticket.cancel():
                return False
            ticket.result()
        return True

    def release(self, group=None):
        with self.condition:
            if self.active <= 0 or (group is not None and group.active <= 0):
                raise ValueError('Compiler admission released without acquisition')
            self.active -= 1
            if group is not None:
                group.active -= 1
            ready = self._ready()
        for grant in ready:
            grant.set_result(None)

    @contextmanager
    def capacity(self, count):
        token = object()
        with self.condition:
            self.requests[token] = count
            ready = self._ready()
        for grant in ready:
            grant.set_result(None)
        try:
            yield
        finally:
            with self.condition:
                self.requests.pop(token)


class Group:
    def __init__(self, admission, limit):
        self.admission = admission
        self.limit = limit
        self.active = 0

    def request(self):
        return self.admission.request(self, background=True)

    def finish(self, ticket):
        if not ticket.cancel():
            ticket.result()
            self.admission.release(self)
