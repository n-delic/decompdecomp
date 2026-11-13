"""The project page's live feed, sent only as far as it changed.

compact(): opt-in live responses retain event tails and omit unchanged display sections. The overview's version comes
from the engine (engine.live_overview: a digest taken once per rebuild), so an unchanged poll never serialises it.

Watcher: what /api/stream compares between its checks (engine.live_signature, plus the helpers' sessions), shared by
every stream open on one project so a second window costs nothing more.
"""
import hashlib
import json
import threading
import time

SECTIONS = ('overview', 'claims', 'sessions')


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, separators=(',', ':'), default=str).encode()).hexdigest()[:24]


def compact(out, previous):
    try:
        previous = json.loads(previous or '{}')
    except (ValueError, TypeError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    result, versions = dict(out), dict(out.get('versions') or {})
    for key in SECTIONS:
        if key not in out:
            continue  # not asked for: the page keeps what it has
        versions[key] = versions.get(key) or digest(out[key])
        if previous.get(key) == versions[key]:
            result.pop(key)
    result['versions'] = versions
    return result


def parse_sections(text):
    """`only=feed,claims`: the live feed's sections the page asks for (None: all of them)."""
    if not text:
        return None
    wanted = {part.strip() for part in str(text).split(',')} & {'feed', 'claims', 'overview', 'sessions'}
    return wanted or None


def sessions_signature(manager, slug) -> str:
    """The helpers working a project, from memory: what the page's agent cards and logs show moves this."""
    rows = []
    for s in list(getattr(manager, 'sessions', {}).values()):
        if getattr(s, 'slug', None) != slug:
            continue
        feed = getattr(s, 'feed', None) or []
        rows.append((s.id, getattr(s, 'state', None), feed[-1].get('seq') if feed else 0, getattr(s, 'why', None),
                     getattr(s, 'last_message', None), getattr(s, 'runs', 0), getattr(s, 'restarts', 0),
                     getattr(s, 'error', None), getattr(s, 'provider_waiting', None), getattr(s, 'until', None),
                     getattr(s, 'startup_stage', None), round(getattr(s, 'usd', 0) or 0, 4), getattr(s, 'tokens', None),
                     getattr(s, 'model', None), getattr(s, 'label', None)))
    return hashlib.sha256(repr(sorted(rows, key=lambda r: str(r[0]))).encode()).hexdigest()[:16]


class Watcher:
    """Each project's latest signature, read at most once per `fresh` seconds however many streams watch it."""

    def __init__(self, net, manager, fresh=0.5):
        self.net, self.manager, self.fresh = net, manager, fresh
        self.lock = threading.Lock()
        self.latest = {}

    def signature(self, slug) -> dict:
        now = time.monotonic()
        with self.lock:
            seen = self.latest.get(slug)
            if seen and now - seen[0] < self.fresh:
                return seen[1]
        with self.net.progress.view():
            value = dict(self.net.live_signature(slug))
        value['sessions'] = sessions_signature(self.manager, slug)
        with self.lock:
            self.latest[slug] = (time.monotonic(), value)
            for other in [k for k, (t, _) in self.latest.items() if now - t > 60]:
                self.latest.pop(other, None)
        return value


def changed(before, after) -> list[str]:
    """The sections whose tokens moved ("event" is a cursor, not a section)."""
    return [key for key in after if key != 'event' and (before or {}).get(key) != after[key]]


def message(kind, data) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
