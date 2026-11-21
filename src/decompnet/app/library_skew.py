"""Pick up an upgraded decomprepair / decompassets without restarting the app.

decompdecomp imports the libraries' modules lazily, one when its job first needs it. When the library on disk is
upgraded while the app runs (an update, `uv sync`, a pull), a module imported earlier is the old version and one imported
later is the new one: a deep search then stopped at "module has no attribute 'verified_variants'", reported as a bug.

The versions are noted when this module is first imported (the app's startup). `ensure()`, called when a deep search or
a repair request starts, compares them with what is installed now and, if they differ, reloads the libraries' loaded
modules in place, dependencies first, so every reference to a module object (the app's `from decomprepair import x`)
sees the new code, and bumps `EPOCH`, which makes the proposal subprocesses (draft_repairs) start fresh instead of
reusing an old one. Names an app module bound directly (`from decomprepair.x import f`) stay the old function until the
app restarts; they are small helpers, and the old and new copies agree. A reload that fails (files half written)
leaves the old code running and asks for a restart.
"""
import importlib
import sys
import threading
import time
from importlib import metadata

from ..network import NetworkError

PACKAGES = ('decomprepair', 'decompassets')
CHECK_SECONDS = 5  # `ensure` reads the installed versions at most this often
LOCK = threading.RLock()
EPOCH = 0
_next_check = 0.0


def _installed(package):
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


LOADED = {package: _installed(package) for package in PACKAGES}


def installed_versions(installed=_installed):
    return tuple((package, installed(package)) for package in PACKAGES)


def changed(loaded=None, installed=_installed):
    """[(package, version in this process, version on disk now)] for each library that differs."""
    loaded = LOADED if loaded is None else loaded
    return [(package, was, installed(package)) for package, was in loaded.items()
            if was and installed(package) and installed(package) != was]


def _loaded_modules(package):
    return [name for name in list(sys.modules) if name == package or name.startswith(package + '.')]


def _order(package, names):
    """The loaded modules of `package`, each after the modules whose names it holds (found from what it defines)."""
    prefix = package + '.'
    needs = {}
    for name in names:
        module = sys.modules.get(name)
        found = set()
        for value in list(vars(module).values()) if module else ():
            origin = value.__name__ if hasattr(value, '__path__') or type(value).__name__ == 'module' else getattr(value, '__module__', None)
            if isinstance(origin, str) and origin != name and (origin == package or origin.startswith(prefix)) and origin in names:
                found.add(origin)
        needs[name] = found
    order, state = [], {}

    def visit(name):
        if state.get(name):
            return
        state[name] = 1
        for dependency in sorted(needs[name]):
            visit(dependency)
        order.append(name)
    for name in sorted(names):
        visit(name)
    return order


def refresh(packages=PACKAGES, installed=_installed):
    """Reload the loaded modules of each library from disk. Returns the number reloaded."""
    global EPOCH
    count = 0
    with LOCK:
        importlib.invalidate_caches()
        for package in packages:
            names = _loaded_modules(package)
            for name in _order(package, set(names)):
                module = sys.modules.get(name)
                if module is None:
                    continue
                try:
                    importlib.reload(module)
                except ModuleNotFoundError:
                    sys.modules.pop(name, None)  # a module the new version no longer has
                    continue
                count += 1
            LOADED[package] = installed(package)
        EPOCH += 1
    return count


def ensure(*, force=False, installed=_installed, packages=PACKAGES):
    """Reload the libraries if they were upgraded since this process loaded them; cheap otherwise."""
    global _next_check
    now = time.monotonic()
    if not force and now < _next_check:
        return False
    _next_check = now + CHECK_SECONDS
    skew = changed({package: LOADED.get(package) for package in packages}, installed)
    if not skew:
        return False
    with LOCK:
        skew = changed({package: LOADED.get(package) for package in packages}, installed)
        if not skew:
            return False
        try:
            refresh(packages, installed)
        except Exception as error:  # a half-written upgrade: keep what is running and say what to do
            names = ', '.join(f'{package} {was} is loaded but {now} is installed' for package, was, now in skew)
            raise NetworkError(f'decompdecomp could not load its updated library ({names}): {error}. Wait for the update '
                               'to finish and retry; if it keeps failing, quit and start the app again.') from error
    return True
