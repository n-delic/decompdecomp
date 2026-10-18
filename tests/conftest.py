import os
import tempfile

# the apps the tests start never look for updates on the network (tests/test_updates.py checks that part offline)
os.environ.setdefault("DECOMP_UPDATES", "off")
# nothing a test starts may reach the user's own app or projects: GET /api/agents/detected lists agents' models by
# running the agent CLIs installed on this computer, and Antigravity's `decomp mcp` (the user's settings) started the
# user's real app, with this test environment (updates off), while it was restarting into an update
os.environ["DECOMP_HOME"] = tempfile.mkdtemp(prefix="decomp-tests-")
# nothing a test runs opens a window or a tab on the user's screen (app/window.py, webbrowser both honour it)
os.environ.setdefault("BROWSER", "true")
os.environ.setdefault("DECOMP_TRAY", "0")  # nor a tray icon


def pytest_sessionfinish(session, exitstatus):
    """A bridge (`decomp mcp`) starts the app and leaves it running, as it does for the user: stop the throwaway one."""
    import contextlib
    import io
    import shutil

    from decompnet.app import cli

    with contextlib.redirect_stdout(io.StringIO()):
        cli.stop(None)
    shutil.rmtree(os.environ["DECOMP_HOME"], ignore_errors=True)
