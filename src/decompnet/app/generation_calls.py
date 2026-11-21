"""Proposal generation that reads the project (symbols, original bytes), run in the proposal subprocess.

The repair families are pure Python over the source, the target's assembly and the diff rows, but some also look a
symbol up or read the original bytes at one. In the server's interpreter they could not be cut off (the per-family timer
needs a main thread), could not be killed on Stop, competed with the interface for the interpreter lock, and kept the
library version the server had loaded. Here the child runs them and asks the app, over its pipes, for the few things it
needs: `resolve(symbol)`, `symbols.by_name.get(name)` and `read_data(symbol, width)`. The app answers from the project
it owns; the child never touches the database. An input that can't be sent as JSON runs the call in the server as before.
"""
import json
import logging
import time
from dataclasses import asdict
from importlib import import_module

log = logging.getLogger('decompdecomp')
IN_SERVER = False  # tests that stub a library function set this: the stub lives in their process, not the child's

# the calls that may run in the child, and where they live
CALLS = {
    'diff_gated_repairs.proposals': ('decompnet.app.diff_gated_repairs', 'proposals'),
    'repair_cleanup.variants': ('decompnet.app.repair_cleanup', 'variants'),
    'frame_plateau.proposals': ('decompnet.app.frame_plateau', 'proposals'),
    'combined_store_repairs.variants': ('decomprepair.combined_store_repairs', 'variants'),
    'probe.callbacks': ('decompnet.app.generation_calls', '_probe'),  # what the app answers, for the tests
}


def _probe(symbol, *, resolve=None, symbols=None, read_data=None, **_ignored):
    found = symbols.by_name.get(symbol) if symbols else None
    return [['resolve', str(resolve(symbol)) if resolve else ''], ['by_name', found.name if found else ''],
            ['missing', str(symbols.by_name.get('nothing') if symbols else '')],
            ['read', (read_data(symbol, 4) or b'').hex() if read_data else ''], ['pid', str(__import__('os').getpid())]]


def _target(name):
    """(module, function) of an allowed call: the few app-side ones listed, or a public function of decomprepair."""
    if name in CALLS:
        return CALLS[name]
    module, _, function = name.rpartition('.')
    if module.startswith('decomprepair.') and function and not function.startswith('_'):
        return module, function
    raise ValueError('not a proposal call: ' + name)


class _RemoteNames:
    def __init__(self, ask):
        self.ask = ask

    def get(self, name, default=None):
        found = self.ask('by_name', name)
        if found is None:
            return default
        from decomprepair.symbols import Symbol
        return Symbol(**found)


class RemoteSymbols:
    """The two things the repairs use of the project's symbol table, answered by the app."""

    def __init__(self, ask):
        self.ask = ask
        self.by_name = _RemoteNames(ask)

    def resolve(self, name):
        return self.ask('resolve', name)


def serve(name, args, kwargs, ask):
    """In the child: run call `name`, its `_callbacks` answered by the app through `ask`."""
    if name == 'batch':
        return [serve(item[0], item[1], dict(item[2]) if len(item) > 2 else {}, ask) for item in args]
    module, function = _target(name)
    callbacks = set(kwargs.pop('_callbacks', ()))
    if 'resolve' in callbacks:
        kwargs['resolve'] = lambda symbol: ask('resolve', symbol)
    if 'symbols' in callbacks:
        kwargs['symbols'] = RemoteSymbols(ask)
    if 'read_data' in callbacks:
        def read_data(symbol, width):
            data = ask('read_data', symbol, width)
            return None if data is None else bytes.fromhex(data)
        kwargs['read_data'] = read_data
    for key in ('flags', 'jump_tables'):
        if isinstance(kwargs.get(key), list):
            kwargs[key] = tuple(kwargs[key])
    result = getattr(import_module(module), function)(*args, **kwargs)
    return [list(item) if isinstance(item, (tuple, list)) else item for item in result]


def handlers(resolve=None, symbols=None, read_data=None):
    """In the app: the answers the child may ask for."""
    def by_name(name):
        found = symbols.by_name.get(name) if symbols is not None else None
        return asdict(found) if found is not None else None

    def read(symbol, width):
        data = read_data(symbol, width) if read_data else None
        return None if data is None else bytes(data).hex()
    return {'resolve': (lambda symbol: resolve(symbol) if resolve else None), 'by_name': by_name, 'read_data': read}


def generate(name, args, kwargs, *, resolve=None, symbols=None, read_data=None):
    """[(family, source)] from call `name`, in the proposal subprocess; in this process if the inputs can't be sent."""
    from . import draft_repairs, library_skew, repair_generation
    from .purpose_work import checkpoint
    callbacks = [key for key, value in (('resolve', resolve), ('symbols', symbols), ('read_data', read_data)) if value]
    try:
        if IN_SERVER:
            raise ValueError('in the server')
        request = json.dumps([list(args), {**kwargs, '_call': name, '_callbacks': callbacks}],
                             ensure_ascii=False).encode()
    except (TypeError, ValueError):
        log.info('proposal call %s has inputs the subprocess cannot be sent; running it in the server', name)
        module, function = _target(name)
        extra = {key: value for key, value in (('resolve', resolve), ('symbols', symbols), ('read_data', read_data)) if value}
        return list(getattr(import_module(module), function)(*args, **kwargs, **extra))
    library_skew.ensure()
    deadline = time.monotonic() + max(30, 2 * repair_generation.FAMILY_SECONDS)

    def check():
        checkpoint()
        if time.monotonic() >= deadline:
            raise draft_repairs._ProposalDeadline

    try:
        result = draft_repairs._worker_batch(request, check, handlers(resolve, symbols, read_data))
    except draft_repairs._ProposalDeadline:
        log.warning('proposal call %s took longer than %ss and was stopped', name, round(deadline - time.monotonic()))
        return []
    return [(family, text) for family, text in result['value']]


def library(calls):
    """[result of each] for `calls`, a list of (`decomprepair.module.function`, args), run together in the proposal
    subprocess; each result is the function's return value (usually a list of (family, source) pairs or sources)."""
    from . import draft_repairs, library_skew, repair_generation
    from .purpose_work import checkpoint
    try:
        if IN_SERVER:
            raise ValueError('in the server')
        request = json.dumps([[[call[0], list(call[1]), call[2] if len(call) > 2 else {}] for call in calls], {'_call': 'batch'}],
                             ensure_ascii=False).encode()
    except (TypeError, ValueError):
        return [getattr(import_module(_target(call[0])[0]), _target(call[0])[1])(*call[1], **(call[2] if len(call) > 2 else {})) for call in calls]
    library_skew.ensure()
    deadline = time.monotonic() + max(30, 2 * repair_generation.FAMILY_SECONDS)

    def check():
        checkpoint()
        if time.monotonic() >= deadline:
            raise draft_repairs._ProposalDeadline
    try:
        result = draft_repairs._worker_batch(request, check)
    except draft_repairs._ProposalDeadline:
        log.warning('%d proposal calls took longer than %ss and were stopped', len(calls), round(max(30, 2 * repair_generation.FAMILY_SECONDS)))
        return [[] for _ in calls]
    return result['value']


def pairs(result):
    return [(family, source) for family, source in result]
