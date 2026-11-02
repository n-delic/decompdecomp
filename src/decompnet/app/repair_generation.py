"""Cooperative proposal-generation budgets and bounded raw-stage caches (never verdict caches)."""
import contextvars
import hashlib
import json
import sys
import threading
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass

ACTIVE = contextvars.ContextVar('repair_generation', default=None)
MAX_CACHE_BYTES = 16000000
CACHE_LOCK = threading.RLock()


@dataclass
class State:
    cache: dict
    scope: object
    deadline: float | None
    families: set
    incomplete: bool = False
    accept: object = None
    goal: int = 0
    known: object = None


@contextmanager
def session(net, scope, deadline, families, *, accept=None, goal=0):
    cache = getattr(net, '_repair_generation_cache', None)
    if cache is None:
        cache = net._repair_generation_cache = {}
    state = State(cache, scope, deadline, set(families), accept=accept, goal=goal, known=set())
    token = ACTIVE.set(state)
    try:
        yield state
    finally:
        ACTIVE.reset(token)


def call(function, *args, **kwargs):
    state = ACTIVE.get()
    if state is None:
        return function(*args, **kwargs)
    module = sys.modules.get(function.__module__)
    families = getattr(module, 'FAMILIES', ())
    if families and not state.families.intersection(families):
        return []
    key = hashlib.sha256(repr((state.scope, function.__module__, function.__qualname__, args, kwargs)).encode()).hexdigest()
    if state.goal and len(state.known) >= state.goal:
        state.incomplete = True
        return []
    # Generators that query the network/database must see fresh donor/recipe state. Pure source
    # stages can be shared by workers; trial/eligibility filtering always happens afterwards.
    cacheable=not any(hasattr(arg,'store') for arg in args)
    with CACHE_LOCK:
        cached=state.cache.get(key) if cacheable else None
    if cached is not None:
        result = json.loads(zlib.decompress(cached[0]))
        count(state,result)
        return result
    if state.deadline is not None and time.monotonic() >= state.deadline:
        state.incomplete = True
        return []
    result = list(function(*args, **kwargs))
    count(state,result)
    # Proposals repeat almost all of their source. Compress raw stages so many agents can reuse
    # large-function proposals without retaining hundreds of megabytes of duplicate C strings.
    packed=zlib.compress(json.dumps(result).encode(),level=1)
    if cacheable and len(packed) <= MAX_CACHE_BYTES:
        with CACHE_LOCK:
            state.cache[key] = (packed, len(packed))
            while len(state.cache) > 512 or sum(value[1] for value in state.cache.values()) > MAX_CACHE_BYTES:
                del state.cache[next(iter(state.cache))]
    return result


def count(state, result):
    if state.accept:
        for value in result:
            if state.deadline is not None and time.monotonic()>=state.deadline:
                state.incomplete=True
                break
            if (isinstance(value,(tuple,list)) and len(value)==2 and value[0] in state.families
                and isinstance(value[1],str) and value[1] not in state.known and state.accept(value[0],value[1])):
                state.known.add(value[1])


def exhausted():
    state=ACTIVE.get()
    if state and ((state.goal and len(state.known)>=state.goal)
                  or state.deadline is not None and time.monotonic()>=state.deadline):
        state.incomplete=True
        return True
    return False


def wanted(family):
    state=ACTIVE.get()
    return state is None or family in state.families
