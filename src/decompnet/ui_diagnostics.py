"""Bounded local latency diagnostics, containing route templates and durations only."""
import asyncio
import logging
import threading
import time
from collections import deque

from starlette.routing import Match

from .hang_diagnostics import Watchdog

_clock = time.monotonic
_lock = threading.Lock()
_samples = deque(maxlen=100)
_last_log = {}


def record(kind, seconds, route=''):
    if seconds < .5:return
    with _lock:
        _samples.append({'kind':kind,'seconds':round(seconds,3),'route':route,'time':time.time()})
        key=(kind,route)
        now=time.monotonic()
        should_log=now-_last_log.get(key,float('-inf'))>=60
        if should_log:
            if len(_last_log)>=100:_last_log.clear()
            _last_log[key]=now
    if should_log:logging.getLogger('decompdecomp').warning('%s %s took %.3fs',kind,route,seconds)


def snapshot():
    with _lock:return list(_samples)


async def watch_loop():
    while True:
        began=time.monotonic()
        await asyncio.sleep(1)
        await asyncio.to_thread(record,'Event loop stall',max(0,time.monotonic()-began-1))


class Diagnostics:
    """ASGI wrapper: monitor lifetime follows the server, without another polling thread."""
    def __init__(self, app, *, stack_path=None):self.app,self.stack_path=app,stack_path

    async def __call__(self, scope, receive, send):
        if scope['type']=='lifespan':
            watchdog=pulse=None
            if self.stack_path is not None:
                watchdog=Watchdog(self.stack_path)
                pulse=asyncio.create_task(watchdog.pulse())
                await asyncio.to_thread(watchdog.start)
            task=asyncio.create_task(watch_loop())
            try:await self.app(scope,receive,send)
            finally:
                if watchdog:await asyncio.to_thread(watchdog.close)
                if pulse:
                    pulse.cancel()
                    try:await pulse
                    except asyncio.CancelledError:pass
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
            return
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        began=_clock()
        try:await self.app(scope,receive,send)
        finally:
            elapsed=_clock()-began
            if elapsed>=.5 and scope.get('path','').startswith('/api/'):
                # Only inspect routing for slow API calls, not every poll or MCP stream.
                # Never retain query values, invite tokens or arbitrary raw URLs.
                route=getattr(scope.get('route'),'path','')
                if not route:
                    for candidate in getattr(self.app,'routes',()):
                        if candidate.matches(scope)[0] == Match.FULL:
                            route=getattr(candidate,'path','')
                            break
                label=route or getattr(scope.get('endpoint'),'__name__','API request')
                await asyncio.to_thread(record,'Slow UI request',elapsed,label)
