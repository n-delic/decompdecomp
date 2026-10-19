"""Native window actions without starting WebView2 or touching a running app."""

import importlib.util
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import window


@pytest.fixture
def native_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "System", SimpleNamespace(Action=lambda callback: callback))
    spec = importlib.util.spec_from_file_location("test_native_window", Path(window.__file__).with_name("winwindow.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def native(native_module):
    module = native_module
    api = module.WindowAPI()
    calls = []
    api._view = SimpleNamespace(native=SimpleNamespace(BeginInvoke=lambda action: calls.append(action)))
    api._chrome = SimpleNamespace(SetTheme=lambda *colors: calls.append(colors))
    return api, calls


def test_bridge_keeps_native_window_objects_private(native):
    api, _ = native
    assert {name for name in dir(api) if not name.startswith('_')} == {"set_theme"}


def test_windows_window_keeps_system_styles_and_installs_chrome_before_showing(monkeypatch):
    class Event:
        def __init__(self):
            self.callbacks = []

        def __iadd__(self, callback):
            self.callbacks.append(callback)
            return self

    view = SimpleNamespace(events=SimpleNamespace(before_show=Event(), closing=Event()),
                           native=SimpleNamespace(webview=SimpleNamespace(NavigationStarting=Event())))
    calls = []
    bridge = SimpleNamespace(create_window=lambda *args, **kwargs: calls.append(kwargs) or view, settings={})
    monkeypatch.setitem(sys.modules, "webview", bridge)
    spec = importlib.util.spec_from_file_location("test_native_main", Path(window.__file__).with_name("winwindow.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(module, "_install_chrome", lambda actual: calls.append(actual))
    monkeypatch.setattr(module, "app_id", lambda *_: None)
    monkeypatch.setenv("DECOMP_WINDOW_TOKEN", "private-test-token")
    monkeypatch.setattr(module.sys, "argv", ["winwindow.py", "--url", "http://localhost/", "--icon", "favicon.ico"])

    def start(**kwargs):
        assert kwargs["user_agent"] == "decompdecomp-window/private-test-token"
        assert bridge.settings['ALLOW_DOWNLOADS'] is True
        assert bridge.settings['OPEN_DEVTOOLS_IN_DEBUG'] is False
        assert len(view.events.closing.callbacks) == 1
        assert view.events.closing.callbacks[0]() is False  # JS is asked off the UI thread
        assert len(view.events.before_show.callbacks) == 1
        view.events.before_show.callbacks[0]()

    bridge.start = start
    assert module.main() == 0
    assert calls[0]["frameless"] is False and calls[0]["easy_drag"] is False
    assert calls[0]["min_size"] == (480, 320)
    assert calls[1] is view


@pytest.mark.parametrize("answer,allowed", [("wait", False), ("close", True), (None, True)])
def test_native_close_respects_the_pages_decision(native_module, answer, allowed):
    import threading

    native_module.webview.errors = SimpleNamespace(JavascriptException=RuntimeError, WebViewException=ValueError)
    assert native_module.allow_close(SimpleNamespace(evaluate_js=lambda _: answer), threading.Event()) is allowed


def test_native_close_still_works_when_the_page_failed(native_module):
    import threading

    check = native_module.allow_close
    native_module.webview.errors = SimpleNamespace(JavascriptException=RuntimeError, WebViewException=ValueError)

    def broken(_):
        raise RuntimeError("the page didn't load")
    assert check(SimpleNamespace(evaluate_js=broken), threading.Event())
    closing = threading.Event()
    closing.set()
    assert check(SimpleNamespace(), closing)  # the app's close command bypasses JS


@pytest.mark.parametrize("uri,opens", [("http://127.0.0.1:6474/api/state", False),
                                      ("https://example.com/help", True), ("file:///C:/secret", False)])
def test_other_sites_open_in_the_browser_and_file_navigation_is_blocked(native_module, monkeypatch, uri, opens):
    opened = []
    monkeypatch.setattr(native_module.webbrowser, "open", opened.append)
    event = SimpleNamespace(Uri=uri, Cancel=False)
    native_module.navigation(event, "http://127.0.0.1:6474/")
    assert event.Cancel is (not uri.startswith("http://127.0.0.1:6474/"))
    assert opened == ([uri] if opens else [])


def test_theme_changes_are_applied_on_the_native_ui_thread(native):
    api, calls = native
    colors = ("#22211e", "#eeece6", "#34332e", "#c42b1c")
    api.set_theme(*colors)
    assert len(calls) == 1 and callable(calls[0])
    calls[0]()
    assert calls[1] == colors


@pytest.mark.parametrize("invalid", ["red", "#fff", "#12345678", None, 123])
def test_invalid_theme_colors_never_reach_the_native_frame(native, invalid):
    api, calls = native
    api.set_theme(invalid, "#eeece6", "#34332e", "#c42b1c")
    assert calls == []


@pytest.mark.parametrize("filename", ["favicon.ico", "favicon-dev.ico"])
def test_windows_icon_has_png_images_for_small_and_high_dpi_displays(filename):
    data = (Path(window.__file__).with_name("static") / filename).read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    sizes = []
    for index in range(count):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        width, height = width or 256, height or 256
        assert width == height and planes == 1 and bits == 32
        png = data[offset:offset + length]
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert struct.unpack_from(">II", png, 16) == (width, height)
        sizes.append(width)
    assert sizes == [16, 20, 24, 32, 40, 48, 64, 128, 256]


def test_native_caption_follows_page_and_system_theme_changes():
    node = shutil.which("node")
    if not node:
        pytest.skip("needs node to run the native caption's theme synchronization")
    subprocess.run([node, str(Path(__file__).with_name("window_controls.js"))], check=True,
                   capture_output=True, text=True, timeout=30)


def test_real_windows_frame_hit_tests_resize_limits_and_accessible_controls():
    if sys.platform != "win32" or importlib.util.find_spec("clr") is None:
        pytest.skip("needs Windows and pywebview's .NET runtime")
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("native_chrome.py"))],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hidden_webview2_window_loads_and_respects_close():
    if sys.platform != "win32" or os.environ.get("DECOMP_TEST_WEBVIEW") != "1":
        pytest.skip("set DECOMP_TEST_WEBVIEW=1 on Windows to run the hidden WebView2 smoke test")
    from decompnet.winprocess import Job

    proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("native_window.py"))],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            env=dict(os.environ, DECOMP_WINDOW_TOKEN="smoke-test-secret"))
    job = Job(proc)
    try:
        proc.stdin.write("go\n")
        proc.stdin.flush()
        proc.wait(60)
        output = proc.stdout.read()
    finally:
        job.close()
        proc.wait(10)
        proc.stdin.close()
        proc.stdout.close()
    assert proc.returncode == 0, output
