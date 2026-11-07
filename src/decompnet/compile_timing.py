"""Bounded numeric compiler timings; no source, paths or headers are collected."""
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

from .compile_sessions import current

_lock = threading.Lock()
_totals = {}
_collector = ContextVar('compile_timing_collector', default=None)


def add(name, value):
    if collector := _collector.get():collector.add(name, value)
    if session := current():
        session.metrics[name] = session.metrics.get(name, 0) + value
    with _lock:
        _totals[name] = _totals.get(name, 0) + value


def snapshot():
    with _lock:return dict(_totals)


@contextmanager
def phase(name):
    start = time.monotonic()
    try:yield
    finally:add(name, time.monotonic() - start)


@contextmanager
def collect(collector):
    token = _collector.set(collector)
    try:yield
    finally:_collector.reset(token)
