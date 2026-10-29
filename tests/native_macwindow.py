"""Opt-in hidden WKWebView test against a fresh app; never discovers or runs the owner's agents."""

import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import AppKit
import httpx
import uvicorn
from Foundation import NSURL, NSTimer, NSURLRequest
from starlette.responses import Response

from decompnet.app import collab, macwindow
from decompnet.app.cli import server_config
from decompnet.app.engine import RomNetwork
from decompnet.app.server import build

home = Path(tempfile.mkdtemp(prefix="native-mac-window-", dir=os.environ["DECOMP_HOME"]))
internal = home / ".decomp"
internal.mkdir()
os.environ.update(DECOMP_HOME=str(home), DECOMP_WINDOW_TOKEN="native-mac-test-secret",
                  DECOMP_DETECT_AGENTS="off", DECOMP_UPDATES="off", DECOMP_TRAY="0")
collab.local_addresses = lambda: []
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(128)
port = listener.getsockname()[1]
net = RomNetwork(internal / "decomp.db", home)
mcp, manager, security, _ = build(net, internal, port, "native-mac-test-secret")
@mcp.custom_route("/native-export", methods=["GET"])
async def export_fixture(request):
    if "decompdecomp-window/native-mac-test-secret" not in request.headers.get("user-agent", ""):
        return Response(status_code=401)
    return Response(b"native export bytes", media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="export.zip"'})


served = mcp.streamable_http_app(transport_security=security, host="127.0.0.1")
headers = []


async def capture(scope, receive, send):
    if scope["type"] == "http":
        headers.append(dict(scope["headers"]).get(b"user-agent", b"").decode())
    await served(scope, receive, send)


server = uvicorn.Server(server_config(capture))
thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
thread.start()
url = f"http://127.0.0.1:{port}/"
assert httpx.get(url).status_code == 401, "the app answered a browser without its window's secret"
downloaded = home / "export.zip"


class SavePanel:
    @classmethod
    def savePanel(cls):
        return cls()

    def setNameFieldStringValue_(self, name):
        assert name == "export.zip"

    def setCanCreateDirectories_(self, value):
        pass

    def runModal(self):
        return AppKit.NSModalResponseOK

    def URL(self):
        return NSURL.fileURLWithPath_(str(downloaded))


# Exercise the real WebKit download delegate while choosing a scratch destination without a dialog.
AppKit.NSSavePanel = SavePanel
app, delegate = macwindow.create_window(SimpleNamespace(url=url, title="decompdecomp Mac test", icon="", dev=False))
external = []
macwindow.webbrowser.open = lambda url: external.append(url)
state = {"phase": 0, "busy": False}
failures = []
deadline = time.monotonic() + 25


def evaluate(script, callback):
    state["busy"] = True
    def done(value, error):
        state["busy"] = False
        if error:
            failures.append(str(error))
            delegate.close()
        else:
            callback(value)
    delegate.view.evaluateJavaScript_completionHandler_(script, done)


def tick(_timer):
    try:
        assert time.monotonic() < deadline, f"native app load/close timed out in phase {state['phase']}"
        if state["busy"]:
            return
        phase = state["phase"]
        if phase == 0:
            def loaded(value):
                if value:
                    state["phase"] = 10
            evaluate('Boolean(typeof S !== "undefined" && S.st && typeof decompClose === "function")', loaded)
        elif phase == 10:
            state["phase"] = 11
            evaluate('var a=document.createElement("a");a.href="/native-export";a.download="export.zip";a.click();true',
                     lambda _: None)
        elif phase == 11:
            if not downloaded.exists():
                return
            assert downloaded.read_bytes() == b"native export bytes"
            state["phase"] = 1
        elif phase == 1:
            state["phase"] = 2
            evaluate('var a=document.createElement("a");a.href="https://example.invalid/external";'
                     'a.target="_blank";a.click();true', lambda _: None)
        elif phase == 2:
            if not external:
                return
            assert external == ["https://example.invalid/external"]
            assert "native-mac-test-secret" not in external[0]
            state["phase"] = 21
            state["requests_before"] = len(headers)
            evaluate('window.nativeBeforeReload=true;'
                     'var a=document.createElement("a");a.href="/";a.target="_blank";a.click();true',
                     lambda _: None)
        elif phase == 21:
            if len(headers) <= state["requests_before"]:
                return
            # The first request isn't a loaded page; it can even be the old page's poll, so phase 22 waits
            # until the new document (nativeBeforeReload is gone) has fetched /api/state.
            state["phase"] = 22
            # A request reaching the server doesn't mean WebKit has initialized the new page yet (Intel CI).
            # Keep polling until it is ready, and don't accept the old document while navigation is pending.
            evaluate('Boolean(!window.nativeBeforeReload && typeof S !== "undefined" && S.st '
                     '&& typeof decompClose === "function")',
                     lambda loaded: state.update(phase=3 if loaded else 21))
        elif phase == 22:
            def loaded(value):
                if value:
                    state["phase"] = 3
            evaluate('Boolean(!window.nativeBeforeReload && typeof S !== "undefined" && S.st '
                     '&& typeof decompClose === "function")', loaded)
        elif phase == 3:
            state["phase"] = 31
            evaluate('window.decompClose=()=>"wait"; true', lambda _: None)
        elif phase == 31:
            state["phase"] = 4
            delegate.windowShouldClose_(delegate.window)
        elif phase == 4:
            if delegate.asking:
                return
            # Reaching this tick proves "wait" left the message loop running; the external link kept our page.
            state["phase"] = 5
            evaluate('window.decompClose=()=>"close"; document.title',
                     lambda title: failures.append("lost app page") if "decompdecomp" not in title else None)
        elif phase == 5:
            state["phase"] = 6
            delegate.windowShouldClose_(delegate.window)
    except Exception as error:
        failures.append(str(error))
        delegate.close()


timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.1, True, tick)
delegate.view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
# A hidden window exercises the same delegates and WKWebView without showing a test window on the desktop.
app.run()
timer.invalidate()
server.should_exit = True
thread.join(timeout=5)
assert not failures, failures
assert state["phase"] == 6, f"close bypassed the page's decision: {state}"
assert sum("decompdecomp-window/native-mac-test-secret" in h for h in headers) >= 5, headers
print("native macOS window passed: real app, private requests, external navigation, export download, wait/close")
