"""Advisory diagnostics for saved ROM drafts, separate from the exact match judge.

A private N64 near miss used an uninitialized pointer. One warning-enabled compile per input snapshot
makes that evidence available before more permutations; it never supplies a score or changes project flags.
"""
import hashlib
import json
import logging
import threading

from .. import toolchains
from ..project import compiler_for

log = logging.getLogger('decompdecomp')


def warnings(net, proj, fn):
    """Return cached diagnostics where the pinned compiler supports warning flags, without keeping its object."""
    if not fn['best_source'] or net.fork_slug(proj['slug']):
        return ''  # Imported projects own their build commands; their ordinary build retains its diagnostics.
    cfg = net.cfg(proj)
    settings = compiler_for(cfg, fn['vram'], fn)
    flags = toolchains.diagnostic_flags(settings.get('toolchain', ''))
    if not flags or settings.get('cmd') or not net.is_rom(proj):
        return ''
    header = net._context(proj['id'], fn['unit'])['header']
    key = hashlib.sha256(repr((proj['root'], settings, header, fn['best_source'])).encode()).hexdigest()
    cache = net.__dict__.setdefault('_compiler_preflights', {})
    lock = net.__dict__.setdefault('_compiler_preflight_lock', threading.Lock())
    with lock:
        if key in cache:
            return cache[key]
    diagnostic = {**cfg, 'compiler_ranges': [], 'function_compilers':{}, 'compiler': {**settings, 'flags': [*settings.get('flags', []), *flags]}}
    try:
        result = net._compile({**dict(proj), 'config': json.dumps(diagnostic)}, header, fn['best_source'], fn)
        lines = [line for line in result.log.splitlines() if 'warning' in line.lower() or 'uninitialized' in line.lower()]
        text = '\n'.join(lines)[:1000] if result.ok else ''
    except Exception:  # advisory evidence must never stand between the user and the real judge
        log.exception('compiler warning preflight failed')
        return ''  # Tool failures remain retryable.
    # Different functions may compile concurrently. Only publication/eviction needs the lock.
    with lock:
        cache[key] = text
        while len(cache) > 256:
            del cache[next(iter(cache))]
    return text
