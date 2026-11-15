"""Cooperative proposal-generation budgets and bounded raw-stage caches (never verdict caches)."""
import contextvars
import hashlib
import json
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
        return function(*args, **kwargs)
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
        result = list(function(*args, **kwargs))
    finally:
        state.metrics['generated_stages']+=1;state.generation_depth-=1
        if state.generation_depth==0:state.metrics['generation_ms']+=max(0,(time.monotonic()-started)*1000)
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
