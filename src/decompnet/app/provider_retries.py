"""Recognize sustained provider retries without interrupting a progressing stream."""
import threading
import time

MIN_FAILURES = 3
QUIET_SECONDS = 120


class Retries:
    def __init__(self):
        self.lock = threading.Lock()
        self.message = ''
        self.first = 0
        self.count = 0

    def progress(self):
        with self.lock:
            self.message = ''
            self.count = 0

    def observe(self, message):
        if not message:return ''
        now = time.monotonic()
        with self.lock:
            if message != self.message:
                self.message, self.first, self.count = message, now, 0
            self.count += 1
            return message if self.count >= MIN_FAILURES and now-self.first >= QUIET_SECONDS else ''
