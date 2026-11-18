import os
import re
import tempfile
from pathlib import Path

# Export/build subprocesses change cwd. Keep them on this checkout even when the
# test interpreter is editable to another worktree and PYTHONPATH started relative.
_CHECKOUT = Path(__file__).resolve().parents[1]
_python_paths = [str(_CHECKOUT / "src"), str(_CHECKOUT)]
_python_paths.extend(str(Path(p or ".").resolve()) for p in os.environ.get("PYTHONPATH", "").split(os.pathsep))
os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(_python_paths))

# the apps the tests start never look for updates on the network (tests/test_updates.py checks that part offline)
os.environ.setdefault("DECOMP_UPDATES", "off")
# Post-match compiler analysis is explicitly exercised with fake workers in its own tests.
os.environ["DECOMP_REPAIR_ANALYSIS"] = "off"
# nothing a test starts may reach the user's own app or projects: listing agents' models runs the agent CLIs
# installed on this computer, and Antigravity's settings once started the user's real app through the old
# `decomp mcp` bridge, with this test environment, while it was restarting into an update
os.environ["DECOMP_HOME"] = tempfile.mkdtemp(prefix="decomp-tests-")
# and the app looks for installed agents in the background as it starts: here it only looks where they are, and
# never runs one (agents.detect)
os.environ["DECOMP_DETECT_AGENTS"] = "off"
# nothing a test runs opens a window on the user's screen (app/window.py), a browser tab (webbrowser), or a tray icon
os.environ["DECOMP_WINDOW"] = "0"
# helpers connect to the app itself; tests/test_mcp_gateway.py starts the gateway by hand
os.environ["DECOMP_MCP_GATEWAY"] = "0"
# tests count compiler invocations; one test's stored objects must not answer another's (tests/test_compile_cache.py
# turns the persistent object cache on for itself)
os.environ["DECOMP_OBJECT_CACHE_MB"] = "0"
os.environ["DECOMP_RENDER_WORKERS"] = "0"  # progress renders in this process, except tests/test_render_worker.py
os.environ["DECOMP_DEEP_LANES"] = "0"  # deep passes judge in this process (patched searches), except tests/test_deep_lanes.py
# PS1/PS2 imports would download libretro's title lists (consoles/playstation_titles): tests read an empty folder,
# and tests/test_playstation_titles.py gives itself small lists
os.environ["DECOMPNET_PLAYSTATION_TITLES"] = tempfile.mkdtemp(prefix="decomp-tests-titles-")
os.environ.setdefault("BROWSER", "true")
os.environ.setdefault("DECOMP_TRAY", "0")
# nor the wine prefix the user's app runs Visual C++ 2017 in (toolchains/.wine, a development copy's .wine-dev: #146):
# a test that names no prefix of its own (the own_wine_prefix fixture) gets this one, so a wine program it starts,
# or the server it leaves running, is never in the app's prefix (review of #138: three tests used it). One set before
# the tests (a scratch home's) is kept.
WINE_PREFIX = None if os.environ.get("DECOMPNET_WINE_PREFIX") else tempfile.mkdtemp(prefix="decomp-tests-wine-")
if WINE_PREFIX:
    os.environ["DECOMPNET_WINE_PREFIX"] = WINE_PREFIX


def pytest_sessionfinish(session, exitstatus):
    import shutil
    from pathlib import Path

    shutil.rmtree(os.environ["DECOMP_HOME"], ignore_errors=True)
    if WINE_PREFIX:  # (its server first: one a test left running would hold the folder)
        from decompnet import wine

        wine.stop(Path(WINE_PREFIX))
        shutil.rmtree(WINE_PREFIX, ignore_errors=True)


def _shared_build_modules():
    """Test modules that build into the shared, gitignored projects/<demo>/build/ folder.

    Every xdist worker uses the same checkout, and different modules build the same demo
    with different compilers. Detecting them from the source keeps the list honest as
    modules are added; a false positive only costs a little parallelism.
    """
    hint = re.compile(r"\bbuild\.py\b|test_rom import built")
    folder = Path(__file__).parent
    return {p.name for p in folder.glob("test_*.py")
            if hint.search(p.read_text(encoding="utf-8", errors="replace"))}


def pytest_xdist_make_scheduler(config, log):
    """Keep the demo-building modules on one worker; run everything else in parallel.

    Without this, two workers can build the same demo at the same time and read each
    other's output. The shared modules become one loadscope work unit, so they stay
    serial relative to each other while the rest of the suite spreads across workers.
    """
    from xdist.scheduler.loadscope import LoadScopeScheduling

    shared = _shared_build_modules()

    class SharedBuildScheduling(LoadScopeScheduling):
        def _split_scope(self, nodeid):
            module = nodeid.replace("\\", "/").split("::", 1)[0].rsplit("/", 1)[-1]
            if module in shared:
                return "shared-demo-builds"
            return super()._split_scope(nodeid)

    return SharedBuildScheduling(config, log)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_progress_wait(monkeypatch):
    """A tool call disturbed by a save waits up to 90 s for it (tools.PROGRESS_RETRY_SECONDS); tests that expect the
    disturbance as an error would wait that long, so they get none. Tests of the wait set their own."""
    from decompnet.app import tools
    monkeypatch.setattr(tools, "PROGRESS_RETRY_SECONDS", 0)


# A test that runs away with memory or never returns must not take the machine with it. One test grew a worker to
# 15 GB (14 GB of it compressed, which resident-size checks miss) and kept it for over 15 minutes. Each test process
# watches its own physical footprint and, above the limit, names the test, prints every thread's stack and ends
# itself: the test fails ("worker crashed") and xdist carries on with a new worker. DECOMP_TEST_MEMORY_MB sets the
# limit (0: off). The per-test time limit is pytest-timeout's (addopts).
MEMORY_LIMIT_MB = int(os.environ.get("DECOMP_TEST_MEMORY_MB", "4096"))
_running = {"test": None}
_config = {}


def memory_mb() -> float | None:
    """This process's physical footprint in MB: what it holds in memory including what the system compressed or
    swapped out for it (Activity Monitor's "Memory"). None where it can't be read."""
    import sys
    if sys.platform == "darwin":
        import ctypes
        class Usage(ctypes.Structure):  # rusage_info_v2's leading fields (sys/resource.h)
            _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
                "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups", "pageins", "wired_size",
                "resident_size", "phys_footprint", "proc_start_abstime", "proc_exit_abstime", "child_user_time",
                "child_system_time", "child_pkg_idle_wkups", "child_interrupt_wkups", "child_pageins",
                "child_elapsed_abstime", "diskio_bytesread", "diskio_byteswritten")]
        try:
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            usage = Usage()
            if libproc.proc_pid_rusage(os.getpid(), 2, ctypes.byref(usage)) != 0:
                return None
            return usage.phys_footprint / 2**20
        except OSError:
            return None
    try:
        fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        return sum(int(fields.get(key, "0 kB").split()[0]) for key in ("VmRSS", "VmSwap")) / 1024
    except (OSError, ValueError):
        return None


def _watch_memory(limit_mb, interval=1.0, stop=None):
    import faulthandler
    import sys
    import threading
    import time
    stop = stop or threading.Event()
    while not stop.wait(interval):
        used = memory_mb()
        if used is None or used <= limit_mb:
            continue
        try:  # (pytest captures stderr at the descriptor, and an ended process never hands it back: show it now)
            _config["config"].pluginmanager.getplugin("capturemanager").suspend_global_capture(in_=False)
        except Exception:
            pass
        stream = sys.__stderr__
        stream.write(f"\n{_running['test'] or 'pytest'} used {used:,.0f} MB, over the {limit_mb:,} MB a test may use "
                     "(DECOMP_TEST_MEMORY_MB); ending this test process. Where every thread was:\n")
        stream.flush()
        faulthandler.dump_traceback(file=stream, all_threads=True)
        stream.flush()
        time.sleep(0.2)
        os._exit(70)


def pytest_configure(config):
    _config["config"] = config
    if MEMORY_LIMIT_MB > 0 and memory_mb() is not None:
        import threading
        threading.Thread(target=_watch_memory, args=(MEMORY_LIMIT_MB,), name="test-memory-guard", daemon=True).start()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _running["test"] = item.nodeid
    try:
        yield
    finally:
        _running["test"] = None
