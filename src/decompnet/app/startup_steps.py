"""Say on the console what the app is doing while it starts, and how long each step took.

Starting a home with hundreds of projects took two minutes with nothing printed but the first log line: the user
couldn't tell a slow database upgrade from a hung app. Each step prints when it begins and, once it ends, how long
it took; the times go to the log too, so a slow start can be traced afterwards."""

import logging
import time
from contextlib import contextmanager

log = logging.getLogger("decompdecomp")
STARTED = time.monotonic()


@contextmanager
def step(label: str):
    """Print `label…` now and its duration when the block ends (or that it failed)."""
    print(f"  {label}…", flush=True)
    began = time.monotonic()
    try:
        yield
    except BaseException:
        log.info("startup: %s failed after %.1fs", label, time.monotonic() - began)
        raise
    took = time.monotonic() - began
    log.info("startup: %s took %.1fs", label, took)
    if took >= 1:
        print(f"    done in {took:.1f}s", flush=True)


def ready() -> None:
    took = time.monotonic() - STARTED
    log.info("startup: ready after %.1fs", took)
    print(f"  ready after {took:.1f}s", flush=True)


_open: list = []


def begin(label: str) -> None:
    """For straight-line code: end the step `begin` opened before, if any, and start `label`."""
    end()
    cm = step(label)
    cm.__enter__()
    _open.append(cm)


def end() -> None:
    while _open:
        _open.pop().__exit__(None, None, None)
