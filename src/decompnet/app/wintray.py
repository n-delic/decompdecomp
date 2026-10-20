"""Windows' notification-area icon, in its own WinForms process so the server needs no UI message loop.

The parent keeps stdin open. EOF removes the icon and ends this process, including when the app crashes.
stdout carries only ready/open/quit; the server performs those actions on its own threads.
"""

from __future__ import annotations

import site
import sys
import threading
from pathlib import Path

site.addsitedir(str(Path(__file__).resolve().parents[2]))


def create_icon(title, on_open, on_quit, icon_path=None):
    """Build the notification-area menu without showing it; main owns its lifetime."""
    import clr

    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System.Drawing import Icon
    from System.Windows.Forms import ContextMenuStrip, NotifyIcon, ToolStripMenuItem, ToolStripSeparator

    menu = ContextMenuStrip()
    open_item = ToolStripMenuItem(f"Open {title}")
    quit_item = ToolStripMenuItem(f"Quit {title}")
    open_item.Click += lambda *_: on_open()
    quit_item.Click += lambda *_: on_quit()
    menu.Items.Add(open_item)
    menu.Items.Add(ToolStripSeparator())
    menu.Items.Add(quit_item)
    icon = NotifyIcon()
    icon.Icon = Icon(str(icon_path or Path(__file__).with_name("static") / "favicon.ico"))
    icon.Text = title[:63]
    icon.ContextMenuStrip = menu
    icon.DoubleClick += lambda *_: on_open()
    return icon


def _run() -> None:
    icon = create_icon(sys.argv[1], lambda: print("open", flush=True), lambda: print("quit", flush=True), sys.argv[2])
    from System.Windows.Forms import Application, ApplicationContext, Timer

    Application.EnableVisualStyles()
    context, ended = ApplicationContext(), threading.Event()

    def parent():
        sys.stdin.read()
        ended.set()

    def tick(*_):
        if ended.is_set():
            icon.Visible = False
            context.ExitThread()

    timer = Timer()
    timer.Interval = 250
    timer.Tick += tick
    threading.Thread(target=parent, daemon=True, name="tray-parent").start()
    try:
        icon.Visible = True
        timer.Start()
        print("ready", flush=True)
        Application.Run(context)
    finally:
        timer.Dispose()
        icon.Visible = False
        icon.Icon.Dispose()
        icon.ContextMenuStrip.Dispose()
        icon.Dispose()
        context.Dispose()


def main() -> None:
    import clr

    clr.AddReference("System.Threading")
    from System.Threading import ApartmentState, Thread, ThreadStart

    thread = Thread(ThreadStart(_run))
    thread.SetApartmentState(ApartmentState.STA)
    thread.Start()
    thread.Join()


if __name__ == "__main__":
    main()
