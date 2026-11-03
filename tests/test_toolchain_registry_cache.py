"""toolchains.registry() builds its Toolchain objects once and reuses them.

_registry_data() is already process-cached, and the registry's contents never change at runtime, but registry()
rebuilt a fresh dict of Toolchain dataclasses on every call. get() calls registry() on every compile and consoles
read it constantly, so under many agents that per-call rebuild (Toolchain.__post_init__ x every entry) showed up as
~5% of CPU. The registry must be built once and handed back on later calls.
"""
from decompnet import toolchains
from decompnet.toolchains import Toolchain


def test_registry_is_built_once_and_reused():
    first = toolchains.registry()
    second = toolchains.registry()
    assert first is second, "registry() must return the same built dict, not rebuild it every call"


def test_registry_shape_is_preserved():
    reg = toolchains.registry()
    assert reg, "registry must not be empty"
    for tid, tc in reg.items():
        assert isinstance(tc, Toolchain)
        assert tc.id == tid
