"""Say "restart" when a library was updated under the running app, instead of failing halfway through a job.

decompdecomp imports decomprepair's modules lazily, one when its job first needs it. If the library on disk is
upgraded while the app runs (an update, `uv sync`, a pull), a module imported earlier is the old version and one
imported later is the new one: a deep search then stopped at "module has no attribute 'verified_variants'", reported as
a bug in decompdecomp. The versions are noted when this module is first imported (the app's startup) and compared
with what is installed now, when a long job starts.
"""
from importlib import metadata

from ..network import NetworkError

PACKAGES = ('decomprepair', 'decompassets')


def _installed(package):
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


LOADED = {package: _installed(package) for package in PACKAGES}


def changed(loaded=None, installed=_installed):
    """[(package, version in this process, version on disk now)] for each library that differs."""
    loaded = LOADED if loaded is None else loaded
    return [(package, was, installed(package)) for package, was in loaded.items()
            if was and installed(package) and installed(package) != was]


def check(loaded=None, installed=_installed):
    skew = changed(loaded, installed)
    if skew:
        names = ', '.join(f'{package} {was} is loaded but {now} is installed' for package, was, now in skew)
        raise NetworkError('decompdecomp was updated while it was running (' + names + '). Quit and start it again; '
                           'a search started now could stop partway through on code the old version lacks.')
