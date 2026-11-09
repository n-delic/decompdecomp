import os
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
