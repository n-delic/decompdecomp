"""Run the repair families the server calls in the proposal subprocess, whoever calls them.

`generation_calls` moves the deep search's calls one by one. The server has some hundred more call sites into the repair
families (iterative repairs, the permuter, preflight probes, coverage runs). Editing each would be error-prone, and a new
one would call the library in the server again. Instead the entry points themselves, the functions named `variants`,
`*variants`, `proposals`, `rewrite*`, `machine_drafts` and `mutate` in decomprepair and in the app's `*_repairs`
modules, are replaced in the server by a stub that sends the call (arguments pickled) to the proposal subprocess and
returns exactly what the function returned. The two processes run the same code, so pickle is safe on this private pipe,
and it keeps tuples, sets and dataclasses as they were.

What stays in the server, calling the function itself as before: arguments that can't be pickled (the network, a
database row, a closure), calls larger than MAX_BYTES, generators, a subprocess that can't start, and the child itself.
An exception the function raised is raised again, as the same type. Lists, dicts and sets the function filled in place
(an out-parameter such as `labels`) are updated from the child's copies afterwards. Stubs are put on a module when it is imported,
including when the library is reloaded in place.
"""
import base64
import functools
import importlib.abc
import importlib.machinery
import inspect
import logging
import os
import pickle
import re
import sys
import time

from ..network import NetworkError

log = logging.getLogger('decompdecomp')
IN_WORKER = os.environ.get('DECOMP_PROPOSAL_WORKER') == '1'
ENABLED = False
MAX_BYTES = 4_000_000
DEADLINE_SECONDS = 600
ENTRY = re.compile(r'(\w*variants|proposals|rewrite\w*|machine_drafts|machine_draft_copies|mutate)\Z')
EXTRA = {'decomprepair.switch_loop_repairs.combine', 'decomprepair.normalization_repairs.extend_frame',
         'decomprepair.repair_localisation.proposals'}
EXCLUDED = ('decompnet.app.draft_repairs', 'decompnet.app.generation_calls', 'decompnet.app.offload',
            'decompnet.app.deep_proposals', 'decompnet.app.repair_generation')  # these already run in the subprocess
APP_SUFFIX = '_repairs'
ORIGINALS = {}


def _is_function(function):
    """A Python function, or one compiled with Cython: `inspect.isfunction` says no to a compiled one, and the repair
    families the app compiles (app/native_library.py) then ran in the server instead of the subprocess."""
    return inspect.isfunction(function) or type(function).__name__ == 'cython_function_or_method'


def selected(module, name, function):
    """Whether `module.name` is an entry point to run in the subprocess."""
    if not _is_function(function) or function.__module__ != module or name.startswith('_'):
        return False
    if inspect.isgeneratorfunction(function) or getattr(function, '__offloaded__', False):
        return False
    if module in EXCLUDED:
        return False
    qual = f'{module}.{name}'
    if qual in EXTRA:
        return True
    if module.startswith('decomprepair.'):
        return bool(ENTRY.match(name))
    return module.startswith('decompnet.app.') and module.rsplit('.', 1)[1].endswith(APP_SUFFIX) and bool(ENTRY.match(name))


def _remote(qual, original, args, kwargs):
    """The function's result from the proposal subprocess; `original(*args, **kwargs)` here if it can't be sent."""
    from . import draft_repairs, library_skew
    from .purpose_work import checkpoint
    try:
        payload = pickle.dumps((qual, args, kwargs), protocol=5)
    except Exception:  # noqa: BLE001 (an argument that can't be pickled: the network, a row, a closure)
        return original(*args, **kwargs)
    if len(payload) > MAX_BYTES:
        return original(*args, **kwargs)
    library_skew.ensure()
    deadline = time.monotonic() + DEADLINE_SECONDS

    def check():
        checkpoint()
        if time.monotonic() >= deadline:
            raise draft_repairs._ProposalDeadline
    try:
        line = draft_repairs._worker_batch(b'P' + base64.b64encode(payload), check)
    except draft_repairs._ProposalDeadline:
        raise NetworkError(f'The repair family {qual} took longer than {DEADLINE_SECONDS} seconds and was stopped.') from None
    except OSError:
        log.warning('the proposal subprocess could not start; %s runs in the server', qual)
        return original(*args, **kwargs)
    answer = pickle.loads(base64.b64decode(line[1:]))
    if answer[0] == 'ok':
        _copy_back(args, answer[2])
        _copy_back(tuple(kwargs.values()), answer[3])
        return answer[1]
    if answer[0] == 'error':
        raise answer[1]
    return original(*args, **kwargs)  # the result could not be sent back


def _copy_back(before, after):
    """A function may fill a list, dict or set it was given (`labels`, `choices`): the child's copies update ours."""
    for mine, theirs in zip(before, after):
        if type(mine) is not type(theirs) or mine == theirs:
            continue
        if isinstance(mine, list):
            mine[:] = theirs
        elif isinstance(mine, dict):
            mine.clear()
            mine.update(theirs)
        elif isinstance(mine, set):
            mine.clear()
            mine.update(theirs)


def _stub(module, name, original):
    qual = f'{module}.{name}'

    @functools.wraps(original)
    def offloaded(*args, **kwargs):
        if IN_WORKER or not ENABLED:
            return original(*args, **kwargs)
        return _remote(qual, original, args, kwargs)
    offloaded.__offloaded__ = True
    offloaded.__wrapped__ = original
    ORIGINALS[qual] = original
    return offloaded


def wrap_module(module):
    """Replace the entry points of `module` (an imported module object) with stubs; returns how many."""
    count = 0
    name = module.__name__
    for attribute, function in list(vars(module).items()):
        if selected(name, attribute, function):
            setattr(module, attribute, _stub(name, attribute, function))
            count += 1
    return count


def serve(line):
    """In the child: run one pickled call and answer with a pickled ('ok', result[, arguments]) / ('error', exception)."""
    qual, args, kwargs = pickle.loads(base64.b64decode(line))
    module, _, name = qual.rpartition('.')
    if not (qual in EXTRA or module.startswith(('decomprepair.', 'decompnet.app.'))) or name.startswith('_'):
        return pickle.dumps(('error', ValueError('not a proposal call: ' + qual)), protocol=5)
    function = getattr(importlib_import(module), name)
    try:
        result = function(*args, **kwargs)
        return pickle.dumps(('ok', result, args, tuple(kwargs.values())), protocol=5)
    except Exception as error:  # noqa: BLE001 (re-raised in the server as the same type)
        try:
            return pickle.dumps(('error', error), protocol=5)
        except Exception:  # noqa: BLE001
            return pickle.dumps(('error', RuntimeError(f'{type(error).__name__}: {error}')), protocol=5)


def importlib_import(name):
    import importlib
    return importlib.import_module(name)


class _Loader(importlib.abc.Loader):
    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        wrap_module(module)


class _Finder(importlib.abc.MetaPathFinder):
    """Wraps the entry points of every decomprepair and app repair module as it is (re)imported."""

    def find_spec(self, fullname, path, target=None):
        if not (fullname.startswith('decomprepair.') or fullname.startswith('decompnet.app.')):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and spec.loader is not None and hasattr(spec.loader, 'exec_module'):
            spec.loader = _Loader(spec.loader)
        return spec


def install():
    """Start sending the entry points to the proposal subprocess (the server calls this; the child never does)."""
    global ENABLED
    if IN_WORKER:
        return 0
    ENABLED = True
    if not any(isinstance(finder, _Finder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
    return sum(wrap_module(module) for name, module in list(sys.modules.items())
               if module is not None and (name.startswith('decomprepair.') or name.startswith('decompnet.app.')))


def uninstall():
    """Put the original functions back (tests)."""
    global ENABLED
    ENABLED = False
    for qual, original in list(ORIGINALS.items()):
        module, _, name = qual.rpartition('.')
        if module in sys.modules:
            setattr(sys.modules[module], name, original)
    ORIGINALS.clear()
    sys.meta_path[:] = [finder for finder in sys.meta_path if not isinstance(finder, _Finder)]
