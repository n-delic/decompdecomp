"""A compile whose process leaves a grandchild holding the output pipe open — as a detached wineserver does for
the MSVC toolchains — must still return within the timeout and must NOT leak a global `_SLOTS` compile permit.
Each leaked permit removes one of the (cpu-1) compile slots; once the pool is exhausted every agent's compile
blocks on `_SLOTS.acquire`, seen live as `try_compiler` hanging the full 900s MCP timeout while agents reported
"the judge keeps failing". The background/autopilot path enters `cancellable()` (Path 2 in compiler._exec),
whose post-kill `communicate()` drains pipes a grandchild still holds; _SLOTS is shared across all projects, so
such a wine-side leak starves the PS2 agents too."""

import threading
from pathlib import Path

import pytest

from decompnet import compiler
from decompnet.project import compiler_config


def _hang_cfg(root: Path):
    # a backgrounded grandchild inherits stdout/stderr and outlives the compiler (like a detached wineserver),
    # then the compiler itself hangs so the timeout path fires
    script = root / "hang.sh"
    script.write_text("#!/bin/sh\nsleep 60 &\nsleep 60\n", encoding="utf-8")
    script.chmod(0o755)
    return compiler_config(root, {"compiler": {"cmd": [str(script), "{src}", "{obj}"],
                                               "timeout": 1, "sandbox": "none"}})


def _run_once(cfg, cancellable: bool):
    box = {}

    def target():
        if cancellable:
            with compiler.cancellable(lambda: None):  # the background-job path, inside this thread's context
                box["r"] = compiler.compile_source(cfg, "", "int f(void){return 0;}", trusted=True)
        else:
            box["r"] = compiler.compile_source(cfg, "", "int f(void){return 0;}", trusted=True)

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(timeout=20)
    return th, box


@pytest.mark.skipif(not Path("/bin/sh").exists(), reason="POSIX shell fixture")
@pytest.mark.parametrize("cancellable", [False, True], ids=["default", "cancellable"])
def test_hanging_compile_releases_its_slot(tmp_path, cancellable):
    cfg = _hang_cfg(tmp_path)
    before = compiler._SLOTS._value
    th, box = _run_once(cfg, cancellable)
    assert not th.is_alive(), "compile_source hung past timeout: a grandchild held the pipe (slot leaked)"
    assert box["r"].ok is False
    assert compiler._SLOTS._value == before, "a _SLOTS compile permit leaked"
