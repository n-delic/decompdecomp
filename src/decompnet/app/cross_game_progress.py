"""Progress and explicit budgets for cross-game checks, shared by every donor and destination mode."""

import time

from ..network import NetworkError

MAX_SECONDS = 24 * 60 * 60
MAX_ATTEMPTS = 1_000_000


def limits(data):
    """Reject invalid budgets before reserving a worker; long manual passes remain finite and stoppable."""
    result = []
    for key, default, maximum in (("seconds", 20 * 60, MAX_SECONDS), ("attempts", 128, MAX_ATTEMPTS)):
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= maximum or int(value) != value:
            raise NetworkError(f"Choose a whole number of {key} between 1 and {maximum:,}.")
        result.append(int(value))
    return tuple(result)


def report(job, phase, *, game="", function="", donor="", done=None, total=None):
    """Publish a replaceable snapshot rather than retaining one log line per million possible trials."""
    if job is None:
        return
    snapshot = {"phase": phase, "game": game, "function": function, "donor": donor,
                "done": done, "total": total, "attempts": getattr(job, 'cross_tried', 0),
                "attempt_limit": getattr(job, 'reuse_limit', 0),
                "seconds": round(time.monotonic() - getattr(job, 'cross_started', time.monotonic()), 1),
                "second_limit": getattr(job, 'cross_seconds', 0), "confirmed": getattr(job, 'cross_matched', 0),
                "indexed_candidates":getattr(job,'cross_indexed',0),"cached_skips":getattr(job,'cross_cached',0)}
    job.progress = snapshot
    if callback := getattr(job, 'cross_report', None):
        callback(snapshot)
