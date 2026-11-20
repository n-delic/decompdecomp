"""Cooperative proposal-generation budgets and bounded raw-stage caches (never verdict caches)."""
import contextvars
import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field

ACTIVE = contextvars.ContextVar('repair_generation', default=None)
PARTITION = contextvars.ContextVar('repair_generation_partition',default=None)
MAX_CACHE_BYTES = 16000000
CACHE_LOCK = threading.RLock()
# Seconds one repair family may spend generating proposals for one function before it is cut off there and the other
# families go on. decomp-repair 0.5.27 had a pattern that backtracked exponentially over a long run of plain
# statements and held a deep pass for hours. Most families take milliseconds, but record_layout_search legitimately
# takes up to about 30 seconds on a large function (measured on 151 functions of an N64 game), so the cap is four
# times that: it never changes what a working family proposes, and turns a stuck one's hours into two minutes.
FAMILY_SECONDS = float(os.environ.get('DECOMP_REPAIR_FAMILY_SECONDS') or 120)
CUT_OFF = []  # (family, function) cut off in this process since the last `cut_off()`; reported once each
_REPORTED = set()
_DEPTH = threading.local()
log = logging.getLogger(__name__)


class FamilyTimeout(BaseException):
    """Raised inside a family that ran past FAMILY_SECONDS. A BaseException, so a family's own `except Exception`
    can't swallow it."""


def _bounded(function, args, kwargs):
    """Run a family within the tighter of the worker's family cap and the request budget.

    The proposal subprocess runs on its main thread so SIGALRM can interrupt a
    long C regex. Nested stages share the outer timer. The server's request
    threads cannot use this timer.
    """
    depth = getattr(_DEPTH, 'value', 0)
    state = ACTIVE.get()
    maximum = FAMILY_SECONDS
    request_limited = False
    if state and state.deadline is not None:
        remaining = state.deadline - time.monotonic()
        if remaining <= 0:
            state.incomplete = True
            return []
        if maximum <= 0 or remaining < maximum:
            maximum = remaining
            request_limited = True
    timed = (depth == 0 and maximum > 0 and hasattr(signal, 'setitimer')
             and threading.current_thread() is threading.main_thread()
             and signal.getitimer(signal.ITIMER_REAL)[0] == 0)
    if not timed:
        _DEPTH.value = depth+1
        try:return function(*args, **kwargs)
        finally:_DEPTH.value = depth
    def expired(*_):
        raise FamilyTimeout
    previous = signal.signal(signal.SIGALRM, expired)
    _DEPTH.value = 1
    produced = []
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, maximum)
            result = function(*args, **kwargs)
            if not hasattr(result, '__next__'):
                produced = result  # (a list or other value, returned as it was)
            else:
                for value in result:
                    produced.append(value)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except FamilyTimeout:
        # (a timer that fired just as the family returned still lands here, never in the caller)
        if not isinstance(produced, list):
            produced = []
        if request_limited:
            state.incomplete = True
        else:
            stage = function.__module__.rsplit('.', 1)[-1]+'.'+function.__qualname__
            name = args[1] if len(args) > 1 and isinstance(args[1], str) else '?'
            CUT_OFF.append((stage, name))
            report_cut_off(((stage, name),))
        # (a generator's proposals so far are complete sources and stay; a list-returning family gives none)
    finally:
        signal.signal(signal.SIGALRM, previous)
        _DEPTH.value = 0
    return produced


def report_cut_off(pairs):
    """Log, once each, the (family, function) pairs a proposal worker process cut off."""
    for stage, name in pairs:
        if (stage, name) not in _REPORTED:
            _REPORTED.add((stage, name))
            log.warning('Repair family %s was cut off after %ss on %s; the other families go on.', stage,
                        FAMILY_SECONDS, name)


def cut_off():
    """The (family, function) pairs cut off since the last call, and forget them."""
    taken = list(CUT_OFF)
    CUT_OFF.clear()
    return taken


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
    started: float = field(default_factory=lambda:time.monotonic())
    generation_depth: int = 0
    metrics: dict = field(default_factory=lambda:dict(calls=0,generated_stages=0,cache_hits=0,generation_ms=0.,first_eligible_ms=None))


@contextmanager
def session(net, scope, deadline, families, *, accept=None, goal=0):
    with CACHE_LOCK:
        cache = getattr(net, '_repair_generation_cache', None)
        if cache is None:
            cache = net._repair_generation_cache = {}
    state = State(cache, scope, deadline, set(families), accept=accept, goal=goal, known=set())
    token = ACTIVE.set(state)
    try:
        yield state
    finally:
        state.metrics['elapsed_ms']=max(0,(time.monotonic()-state.started)*1000)
        with CACHE_LOCK:
            history=getattr(net,'_repair_generation_metrics',[])
            net._repair_generation_metrics=(history+[dict(state.metrics)])[-64:]
        ACTIVE.reset(token)


def call(function, *args, **kwargs):
    if partition:=PARTITION.get():
        index,count=partition
        stage=function.__module__+'.'+function.__qualname__
        if int.from_bytes(hashlib.sha256(stage.encode()).digest()[:4],'big')%count!=index:return []
        token=PARTITION.set(None)
        try:return _call(function,*args,**kwargs)
        finally:PARTITION.reset(token)
    return _call(function,*args,**kwargs)


def _call(function,*args,**kwargs):
    state = ACTIVE.get()
    if state is None:
        return _bounded(function, args, kwargs)
    state.metrics['calls']+=1
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
        state.metrics['cache_hits']+=1
        result = json.loads(zlib.decompress(cached[0]))
        count(state,result)
        return result
    if state.deadline is not None and time.monotonic() >= state.deadline:
        state.incomplete = True
        return []
    started=time.monotonic();state.generation_depth+=1
    try:
        result = list(_bounded(function, args, kwargs))
    finally:
        state.metrics['generated_stages']+=1;state.generation_depth-=1
        if state.generation_depth==0:state.metrics['generation_ms']+=max(0,(time.monotonic()-started)*1000)
    count(state,result)
    # Proposals repeat almost all of their source. Compress raw stages so many agents can reuse
    # large-function proposals without retaining hundreds of megabytes of duplicate C strings.
    packed=zlib.compress(json.dumps(result).encode(),level=1)
    if cacheable and not state.incomplete and len(packed) <= MAX_CACHE_BYTES:
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
                if state.metrics['first_eligible_ms'] is None:state.metrics['first_eligible_ms']=max(0,(time.monotonic()-state.started)*1000)
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


def measurements(net):
    """Bounded process-local generation telemetry; no C, prompts, scope identifiers or compiler verdicts."""
    with CACHE_LOCK:sessions=[dict(row) for row in getattr(net,'_repair_generation_metrics',[])]
    return dict(sessions=sessions,retained=len(sessions),limit=64,
                note='Discovery timings and raw-stage reuse only; not tokens saved or time inside a compiler. First eligible means a filtered candidate, not a submitted compile.')
