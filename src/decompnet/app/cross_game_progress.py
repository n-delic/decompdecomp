"""Progress snapshots for manual and automatic cross-game checks."""

import time


def report(job, phase, *, game="", function="", donor="", done=None, total=None):
    """Publish a replaceable snapshot rather than retaining one log line per million possible trials."""
    if job is None:
        return
    snapshot = {"phase": phase, "game": game, "function": function, "donor": donor,
                "done": done, "total": total, "attempts": getattr(job, 'cross_tried', 0),
                "attempt_limit": 0 if getattr(job, 'cross_exhaustive', False) else getattr(job, 'reuse_limit', 0),
                "seconds": round(time.monotonic() - getattr(job, 'cross_started', time.monotonic()), 1),
                "second_limit": getattr(job, 'cross_seconds', 0), "confirmed": getattr(job, 'cross_matched', 0),
                "indexed_candidates":getattr(job,'cross_indexed',0),"cached_skips":getattr(job,'cross_cached',0)}
    job.progress = snapshot
    if callback := getattr(job, 'cross_report', None):
        callback(snapshot)
