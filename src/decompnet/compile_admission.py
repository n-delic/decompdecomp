"""Resizable global compiler admission; active leases never replace an occupied gate."""
import threading
from contextlib import contextmanager


class Admission:
    def __init__(self, default):
        self.default = default
        self.active = 0
        self.requests = {}
        self.condition = threading.Condition()

    @property
    def limit(self):return max([self.default, *self.requests.values()])

    def acquire(self, timeout=None):
        with self.condition:
            if not self.condition.wait_for(lambda:self.active < self.limit, timeout):return False
            self.active += 1
            return True

    def release(self):
        with self.condition:
            if self.active <= 0:raise ValueError('Compiler admission released without acquisition')
            self.active -= 1
            self.condition.notify_all()

    @contextmanager
    def capacity(self, count):
        token = object()
        with self.condition:
            self.requests[token] = count
            self.condition.notify_all()
        try:yield
        finally:
            with self.condition:
                self.requests.pop(token)
                self.condition.notify_all()
