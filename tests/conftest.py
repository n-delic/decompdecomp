import os
import tempfile

# the apps the tests start never look for updates on the network (tests/test_updates.py checks that part offline)
os.environ.setdefault("DECOMP_UPDATES", "off")
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


def pytest_sessionfinish(session, exitstatus):
    import shutil

    shutil.rmtree(os.environ["DECOMP_HOME"], ignore_errors=True)
