"""decompdecomp's native Windows window, backed by WebView2 through pywebview.

Linux-only compilers run through WSL, but the app and its window run on Windows. The stdin protocol deliberately
matches gtkwindow.py: the app sends ``present`` and ``close``. The user agent carries the private window token,
so a random browser cannot reach the local app. winchrome.cs provides the native caption
and resize frame while the page supplies their theme colors.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import site
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

# The MSI keeps dependencies in a sibling `packages` directory and starts this process with `-I`, which ignores
# PYTHONPATH. addsitedir also runs pywin32's .pth bootstrap. In a uv installation this resolves to site-packages;
# in a source checkout it resolves to `src`.
site.addsitedir(str(Path(__file__).resolve().parents[2]))

import webview

ASK_PAGE = 'typeof decompClose === "function" ? String(decompClose()) : "close"'


def navigation(event, origin: str) -> None:
    """Keep the app in its window; other sites open in the default browser, without the window's secret."""
    uri = str(event.Uri)
    if urlsplit(uri)[:2] == urlsplit(origin)[:2]:
        return
    event.Cancel = True
    if urlsplit(uri).scheme in ("http", "https"):
        webbrowser.open(uri)


def app_id(window_class: str) -> None:
    """Keep the installed app and the development copy separate on the taskbar."""
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(window_class)


def allow_close(view, closing: threading.Event) -> bool:
    """Use the same close dialog as Linux; a broken page must still let the window close."""
    if closing.is_set():
        return True
    try:
        return view.evaluate_js(ASK_PAGE) != "wait"
    except (webview.errors.JavascriptException, webview.errors.WebViewException):
        logging.getLogger("decompdecomp").warning("the page couldn't answer a close request", exc_info=True)
        return True


def _install_chrome(view):
    """Attach the frame on WinForms' UI thread before showing the window. The small managed helper uses the same
    .NET runtime pywebview already needs; no separate compiler or window toolkit is installed."""
    import clr
    from Microsoft.CSharp import CSharpCodeProvider
    from System.CodeDom.Compiler import CompilerParameters

    options = CompilerParameters()
    options.GenerateInMemory = True
    for assembly in ("System.dll", "System.Drawing.dll", "System.Windows.Forms.dll"):
        options.ReferencedAssemblies.Add(assembly)
    provider = CSharpCodeProvider()
    try:
        result = provider.CompileAssemblyFromSource(options, Path(__file__).with_name("winchrome.cs").read_text())
    finally:
        provider.Dispose()
    if result.Errors.HasErrors:
        raise RuntimeError("Couldn't build the Windows frame: " + "; ".join(str(error) for error in result.Errors))
    clr.AddReference(result.CompiledAssembly.GetName().Name)
    from Decompdecomp import WindowChrome

    return WindowChrome(view.native)


class WindowAPI:
    def __init__(self):
        # The bridge walks public attributes recursively. Keep the native window private so it never inspects
        # WinForms' accessibility objects or exposes the rest of the window API to the page.
        self._view = None
        self._chrome = None

    def set_theme(self, background, foreground, hover, danger):
        """Let the native caption follow the page's appearance, including changes to the system theme."""
        from System import Action

        colors = (background, foreground, hover, danger)
        if self._chrome is None or not all(isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color)
                                          for color in colors):
            return
        self._view.native.BeginInvoke(Action(lambda: self._chrome.SetTheme(*colors)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--title", default="decompdecomp")
    parser.add_argument("--window-class", default="decompdecomp")
    parser.add_argument("--icon", required=True)
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()
    app_id(args.window_class)

    # A user agent works on both window backends, survives long sessions, and keeps the secret out of URLs.
    separator = "&" if "?" in args.url else "?"
    url = f"{args.url}{separator}native=1"
    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False  # --dev enables the inspector without opening another window
    api = WindowAPI()
    view = webview.create_window(
        args.title,
        url,
        width=1440,
        height=920,
        min_size=(480, 320),
        # Retain WS_THICKFRAME and the system menu. Our native caption supplies hit tests, so Windows handles
        # resize cursors, snap, double-click and restoring a maximized window when it is dragged.
        frameless=False,
        easy_drag=False,
        shadow=False,
        text_select=True,
        js_api=api,
    )
    api._view = view

    def chrome():
        api._chrome = _install_chrome(view)
        view.native.webview.NavigationStarting += lambda _sender, event: navigation(event, args.url)

    view.events.before_show += chrome
    closing = threading.Event()

    def on_closing():
        if closing.is_set():
            return True

        def ask():
            if allow_close(view, closing):
                closing.set()
                view.destroy()
        # FormClosing runs on the UI thread. Waiting for JavaScript there would deadlock WebView2.
        threading.Thread(target=ask, daemon=True, name="window-close").start()
        return False

    view.events.closing += on_closing

    def commands() -> None:
        for line in sys.stdin:
            word = line.strip()
            if word == "present":
                view.restore()
                view.show()
            elif word == "close":
                closing.set()
                view.destroy()
                return
        if not closing.is_set():
            closing.set()
            view.destroy()

    threading.Thread(target=commands, daemon=True, name="window-commands").start()
    webview.start(gui="edgechromium", debug=args.dev, icon=args.icon,
                  user_agent=f"decompdecomp-window/{os.environ.get('DECOMP_WINDOW_TOKEN', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
