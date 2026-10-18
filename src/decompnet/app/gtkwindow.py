"""decompdecomp's window: a GTK window around the app's page, on the system's WebKitGTK. No browser involved.

The system's python3 runs this file on its own (window.py starts it), because the GTK bindings come with the
system (python3-gi, gir1.2-webkit2-4.1) and the app's own Python, uv's, can't load them. So it imports nothing of
decompdecomp's: everything it needs comes in its arguments and environment.

- The page is only served to this window: the window's user agent carries the app's per-launch secret
  (DECOMP_WINDOW_TOKEN), and the server answers no request without it. The address alone opens nothing in a browser.
- Closing the window asks the page first (decompClose() in static/js/steering.js): it quits the app, or asks whether
  helpers that are working stop or keep working in the background. Without an answer from the page, it just closes.
- The app talks to it on stdin, one word per line: "present" (bring it to the front), "close".
- Links to other sites open in the user's own browser.
"""

import argparse
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, Gio, GLib, Gtk, WebKit2  # noqa: E402

DEFAULT_SIZE = (1440, 920)
TOKEN_AGENT = "decompdecomp-window/"  # the server looks for this, followed by the secret, in the user agent
PAGE_SAYS_CLOSE = "close"  # decompClose()'s answer when the window may close now (otherwise the page asks the user)
ASK_PAGE = 'typeof decompClose === "function" ? String(decompClose()) : "close"'


class AppWindow:
    def __init__(self, args):
        self.origin = args.url.split("/", 3)[:3]
        self.closing = False
        self.window = Gtk.Window(title=args.title)
        self.window.set_default_size(*DEFAULT_SIZE)
        if args.icon:
            try:
                self.window.set_icon_from_file(args.icon)
            except GLib.Error:  # no SVG loader for GdkPixbuf: the desktop's default icon
                pass
        self.view = WebKit2.WebView()
        settings = self.view.get_settings()
        settings.set_user_agent(f"{settings.get_user_agent()} {TOKEN_AGENT}{os.environ.get('DECOMP_WINDOW_TOKEN', '')}")
        settings.set_enable_developer_extras(args.dev)
        settings.set_enable_write_console_messages_to_stdout(True)  # the page's errors reach the app's log
        self.view.connect("decide-policy", self.on_policy)
        self.window.add(self.view)
        self.window.connect("delete-event", self.on_close_request)
        self.window.connect("key-press-event", self.on_key)
        self.window.connect("destroy", lambda _w: Gtk.main_quit())
        channel = GLib.IOChannel.unix_new(sys.stdin.fileno())
        GLib.io_add_watch(channel, GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP, self.on_command)
        self.view.load_uri(args.url)
        self.window.show_all()

    def on_policy(self, _view, decision, kind):
        """Pages of other sites open in the user's browser, never in this window."""
        if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if uri.split("/", 3)[:3] == self.origin and kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        decision.ignore()
        if uri.startswith(("http://", "https://")):
            Gio.AppInfo.launch_default_for_uri(uri, None)
        return True

    def on_key(self, _window, event):
        """F5 or Ctrl+R reloads the page, as in a browser (the window has no toolbar to do it with)."""
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if event.keyval == Gdk.KEY_F5 or (ctrl and event.keyval in (Gdk.KEY_r, Gdk.KEY_R)):
            self.view.reload()
            return True
        return False

    def on_close_request(self, _window, _event):
        if self.closing:
            return False
        self.view.evaluate_javascript(ASK_PAGE, -1, None, None, None, self.on_page_answer, None)
        return True  # the page decides

    def on_page_answer(self, view, result, _data):
        try:
            answer = view.evaluate_javascript_finish(result).to_string()
        except GLib.Error:  # the page didn't load, or broke: closing must still work
            answer = PAGE_SAYS_CLOSE
        if answer == PAGE_SAYS_CLOSE:
            self.close()

    def close(self):
        self.closing = True
        self.window.destroy()

    def on_command(self, _channel, condition):
        line = sys.stdin.readline() if condition & GLib.IOCondition.IN else ""
        if not line:  # the app is gone: so is its window
            self.close()
            return False
        word = line.strip()
        if word == "present":
            self.window.present()
        elif word == "close":
            self.close()
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--title", default="decompdecomp")
    ap.add_argument("--window-class", default="decompdecomp")
    ap.add_argument("--icon", default="")
    ap.add_argument("--dev", action="store_true", help="allow the web inspector (a development copy)")
    args = ap.parse_args()
    GLib.set_prgname(args.window_class)  # Wayland's app id: the desktop entry's StartupWMClass
    Gdk.set_program_class(args.window_class)  # X11's WM_CLASS
    Gtk.init(None)
    AppWindow(args)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
