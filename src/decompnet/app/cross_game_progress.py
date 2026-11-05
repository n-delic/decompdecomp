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
    if job is None or not getattr(job, "cross_report", None):
        return
    job.cross_report({"phase": phase, "game": game, "function": function, "donor": donor,
                      "done": done, "total": total, "attempts": job.cross_tried,
                      "attempt_limit": job.reuse_limit, "seconds": round(time.monotonic() - job.cross_started, 1),
                      "second_limit": job.cross_seconds, "confirmed": job.cross_matched})
