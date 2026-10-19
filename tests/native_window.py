"""Opt-in WebView2 smoke test against a fresh app with no projects. No window is shown and no agent is run."""

import sys

# The test attaches a kernel job before allowing this process to start WebView2's children.
assert sys.stdin.readline().strip() == "go"

import os  # noqa: E402
import socket  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import uvicorn  # noqa: E402
import webview  # noqa: E402

from decompnet.app import collab, winwindow  # noqa: E402
from decompnet.app.cli import server_config  # noqa: E402
from decompnet.app.engine import RomNetwork  # noqa: E402
from decompnet.app.server import build  # noqa: E402

headers, failures, state = [], [], {}
home = Path(tempfile.mkdtemp(prefix="native-window-", dir=os.environ["DECOMP_HOME"]))
internal = home / ".decomp"
internal.mkdir()
os.environ.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData/Roaming"),
                  LOCALAPPDATA=str(home / "AppData/Local"), XDG_CONFIG_HOME=str(home / ".config"),
                  DECOMP_HOME=str(home), DECOMP_DETECT_AGENTS="off", DECOMP_UPDATES="off", DECOMP_TRAY="0")
collab.local_addresses = lambda: []
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(128)
port = listener.getsockname()[1]
net = RomNetwork(internal / "decomp.db", home)
mcp, manager, security, _ = build(net, internal, port, "smoke-test-secret")
app = mcp.streamable_http_app(transport_security=security, host="127.0.0.1")


async def capture(scope, receive, send):
    if scope["type"] == "http":
        headers.append(dict(scope["headers"]).get(b"user-agent", b"").decode())
    await app(scope, receive, send)


server = uvicorn.Server(server_config(capture))
server_thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
server_thread.start()
original_create, original_start = webview.create_window, webview.start


def create(*args, **kwargs):
    kwargs['hidden'] = True
    view = original_create(*args, **kwargs)
    state.update(view=view, api=kwargs['js_api'])

    return view


def check():
    view = state['view']
    try:
        assert view.events.loaded.wait(30), "WebView2 didn't load the local page"
        assert "decompdecomp" in view.evaluate_js("document.title")
        deadline = time.monotonic() + 15
        while not view.evaluate_js('Boolean(typeof S !== "undefined" && S.st)'):
            assert time.monotonic() < deadline, "the app's scripts didn't load their initial state"
            time.sleep(0.05)
        assert view.evaluate_js('document.documentElement.classList.contains("windows-window")')
        assert not view.native.Visible, "a test window became visible"
        assert state['api']._chrome is not None, "the native frame didn't attach"
        # Exercise the caption protocol without quitting the test server through the real page's /api/close.
        view.evaluate_js('window.answer="wait"; window.decompClose=()=>window.answer')
        view.destroy()
        time.sleep(0.3)
        assert not view.native.IsDisposed, "close bypassed the page's decision"
        view.evaluate_js('answer="close"')
        view.destroy()
        assert view.events.closed.wait(10), "the page allowed close but the window stayed open"
    except Exception as error:  # report failures to pytest after the native message loop ends
        failures.append(str(error))
    finally:
        # Force the final close even if the page never loaded.
        if not view.events.closed.is_set():
            winwindow.allow_close = lambda *_: True
            view.destroy()


def start(**kwargs):
    return original_start(check, **kwargs)


webview.create_window, webview.start = create, start
sys.argv = ['winwindow.py', '--url', f'http://127.0.0.1:{port}/', '--icon',
            str(Path(winwindow.__file__).with_name('static') / 'favicon.ico')]
winwindow.main()
server.should_exit = True
server_thread.join(10)
manager.shutdown()
log = (internal / "logs/decomp.log").read_text(encoding="utf-8")
assert "page:" not in log and "Exception in ASGI application" not in log, log[-4000:]
assert headers and all(value == "decompdecomp-window/smoke-test-secret" for value in headers)
assert not failures, failures
print("hidden WebView2 smoke test passed")
