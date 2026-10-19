"""Real WinForms/Win32 frame checks in a subprocess. The form is never shown and no app server is started."""

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import clr

from decompnet.app.wintray import create_icon
from decompnet.app.winwindow import _install_chrome

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

actions = []
tray_icon = create_icon("decompdecomp dev", lambda: actions.append("open"), lambda: actions.append("quit"))
try:
    assert not tray_icon.Visible and tray_icon.Text == "decompdecomp dev"
    assert tray_icon.ContextMenuStrip.Items.Count == 3
    tray_icon.ContextMenuStrip.Items[0].PerformClick()
    tray_icon.ContextMenuStrip.Items[2].PerformClick()
    assert actions == ["open", "quit"]
finally:
    tray_icon.Icon.Dispose()
    tray_icon.ContextMenuStrip.Dispose()
    tray_icon.Dispose()
from System.Drawing import Color, Point, Size  # noqa: E402
from System.Reflection import BindingFlags  # noqa: E402
from System.Windows.Forms import Button, DockStyle, Form, FormWindowState, Panel, Screen  # noqa: E402

form = Form()
try:
    form.Size = Size(1000, 700)
    content = Panel()
    content.Dock = DockStyle.Fill
    form.Controls.Add(content)  # stand in for WebView2's fill-docked native control
    frame = _install_chrome(SimpleNamespace(native=form))
    from Decompdecomp import WindowChrome

    # All eight resize directions, then caption and buttons; repeat at 200% DPI.
    for scale in (1, 2):
        def hit(x, y, maximized=False, scale=scale):
            edge = 0 if maximized else 6 * scale
            return WindowChrome.HitTest(x * scale, y * scale, 1000 * scale, 700 * scale,
                                        edge, 34 * scale, 46 * scale, maximized)

        for point, expected in [((0, 0), 13), ((500, 0), 12), ((999, 0), 14),
                                ((0, 350), 10), ((999, 350), 11), ((0, 699), 16),
                                ((500, 699), 15), ((999, 699), 17), ((100, 20), 2),
                                ((880, 20), 8), ((925, 20), 9), ((970, 20), 20), ((500, 350), 1)]:
            assert hit(*point) == expected, (point, scale)
        assert hit(0, 350, maximized=True) == 1  # no resizing while maximized
        assert hit(500, 699, maximized=True) == 1
        assert hit(100, 20, maximized=True) == 2  # caption dragging goes through Windows' restore behavior

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    style = user32.GetWindowLongW(form.Handle.ToInt64(), -16)
    assert style & 0x40000  # WS_THICKFRAME: Windows resizing and snapping remain available
    assert style & 0x80000  # WS_SYSMENU: Alt+Space and system commands remain available

    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    point = form.PointToScreen(Point(form.ClientSize.Width // 2, 20))
    packed = (point.X & 0xffff) | ((point.Y & 0xffff) << 16)
    assert user32.SendMessageW(form.Handle.ToInt64(), 0x84, 0, packed) == 2  # actual WM_NCHITTEST -> HTCAPTION

    class MinMaxInfo(ctypes.Structure):
        _fields_ = [(name, wintypes.POINT) for name in ("reserved", "max_size", "max_position", "min_track", "max_track")]

    info = MinMaxInfo()
    user32.SendMessageW(form.Handle.ToInt64(), 0x24, 0, ctypes.addressof(info))
    monitor = Screen.FromHandle(form.Handle)
    assert (info.max_size.x, info.max_size.y) == (monitor.WorkingArea.Width, monitor.WorkingArea.Height)
    assert (info.max_position.x, info.max_position.y) == (
        monitor.WorkingArea.Left - monitor.Bounds.Left, monitor.WorkingArea.Top - monitor.Bounds.Top,
    )

    frame.SetTheme("#22211e", "#eeece6", "#34332e", "#c42b1c")
    assert form.BackColor == Color.FromArgb(34, 33, 30)
    caption = next(control for control in form.Controls if control.GetType().Name == "Caption")
    buttons = list(caption.Controls)
    assert all(isinstance(button, Button) for button in buttons)
    assert {button.AccessibleName for button in buttons} == {"Minimize", "Maximize", "Close"}
    assert content.Top == form.Padding.Top
    assert content.Left == form.Padding.Left
    # Native child hit tests reach the frame, including the maximize region that Windows uses for Snap Layouts.
    for control in (caption, buttons[1]):
        assert user32.SendMessageW(control.Handle.ToInt64(), 0x84, 0, packed) == -1

    # Invoke the registered click handler without showing/focusing a test window. PerformClick intentionally
    # ignores hidden buttons, whereas the same OnClick handler serves both mouse and accessible invocation.
    click = buttons[1].GetType().GetMethod("OnClick", BindingFlags.Instance | BindingFlags.NonPublic)
    from System import EventArgs
    size_changed = form.GetType().GetMethod("OnSizeChanged", BindingFlags.Instance | BindingFlags.NonPublic)
    click.Invoke(buttons[1], [EventArgs.Empty])
    assert form.WindowState == FormWindowState.Maximized
    size_changed.Invoke(form, [EventArgs.Empty])  # hidden forms do not receive the OS's maximize-size message
    assert buttons[1].AccessibleName == "Restore"
    assert form.Padding.Left == 0
    click.Invoke(buttons[1], [EventArgs.Empty])
    assert form.WindowState == FormWindowState.Normal
    size_changed.Invoke(form, [EventArgs.Empty])
    assert buttons[1].AccessibleName == "Maximize"
    assert form.Padding.Left > 0
    assert form.Padding.Top == caption.Height + form.Padding.Left
finally:
    form.Dispose()
