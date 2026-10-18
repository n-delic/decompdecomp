"""decompdecomp's icon in the system tray, on Linux: closing the window leaves the app running there, and its menu
opens the window again or quits.

It speaks the freedesktop StatusNotifierItem protocol (and the dbusmenu one for its menu) over D-Bus with Gio, which
every desktop that shows tray icons this way already has: KDE, COSMIC, GNOME with the AppIndicator extension, Xfce,
Cinnamon. No new dependency: Gio comes with the system's PyGObject. Without a tray host, or without PyGObject, start()
says so, and helpers kept working without the window are reached again by running `decomp`.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Callable

from . import edition

_ITEM = "org.kde.StatusNotifierItem"
_WATCHER = "org.kde.StatusNotifierWatcher"

_XML = """<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  </interface>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
  </interface>
</node>"""

# the menu: id -> properties (dbusmenu)
OPEN_ITEM, SEPARATOR, QUIT_ITEM = 1, 2, 3
_MENU = {OPEN_ITEM: {"label": f"Open {edition.NAME}"}, SEPARATOR: {"type": "separator"},
         QUIT_ITEM: {"label": f"Quit {edition.NAME}"}}
ROOT_ITEM = 0  # dbusmenu's id for the menu itself
DBUSMENU_VERSION = 3
ICON_PIXELS = 32
BUS_CALL_MS = 2000  # asking D-Bus whether a tray host is there
REGISTER_MS = 3000  # registering the icon with the tray host
START_SECONDS = 5  # how long start() waits for the icon to show
_D = ["..X", "XXX", "X.X", "X.X", "XXX"]  # the mark's "d", as on the website's logo


def icon(size: int = 32) -> bytes:
    """The dd mark as ARGB32 (big-endian, as StatusNotifierItem wants): a light d, then a black d on the
    highlighter's block (yellow; a development copy's is blue), scaled into `size` pixels."""
    light, ink, yellow = (0xFF, 0xED, 0xEB, 0xE5), (0xFF, 0x1D, 0x1B, 0x14), (0xFF, *edition.HIGHLIGHT)
    unit = size // 8
    top = (size - 5 * unit) // 2
    px = [[(0, 0, 0, 0)] * size for _ in range(size)]
    for y in range(max(0, top - unit // 2), min(size, top + 5 * unit + unit // 2)):  # the yellow block, with room
        for x in range(int(3.4 * unit), size):
            px[y][x] = yellow
    for left, colour in ((0, light), (4 * unit, ink)):
        for line, row in enumerate(_D):
            for column, cell in enumerate(row):
                if cell == "X":
                    for y in range(top + line * unit, top + (line + 1) * unit):
                        for x in range(left + column * unit, left + (column + 1) * unit):
                            if x < size:
                                px[y][x] = colour
    return bytes(channel for row in px for pixel in row for channel in pixel)


def _gi():
    """Gio and GLib from the system's PyGObject (decompdecomp's own Python sees the system's packages only when asked)."""
    try:
        import gi  # noqa: F401
    except ImportError:
        system = "/usr/lib/python3/dist-packages"
        if sys.platform != "linux" or not os.path.isdir(system):
            return None
        # only for gi: left on the path, it served the system's other packages too, and uvicorn picked up the
        # system's old `websockets` instead of going without it, so the app quit at start whenever a tray showed
        sys.path.append(system)
        try:
            import gi  # noqa: F401
            from gi.repository import Gio, GLib
        except ImportError:
            return None
        finally:
            sys.path.remove(system)
        return Gio, GLib
    from gi.repository import Gio, GLib
    return Gio, GLib


def start(on_open: Callable[[], None], on_quit: Callable[[], None]) -> bool:
    """Show the tray icon (on its own thread). False when there's no tray to show it in."""
    if os.environ.get("DECOMP_TRAY") == "0" or sys.platform != "linux":
        return False
    mods = _gi()
    if mods is None:
        return False
    Gio, GLib = mods
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        has = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                            GLib.Variant("(s)", (_WATCHER,)), GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, BUS_CALL_MS,
                            None)
        if not has.unpack()[0]:
            return False
    except Exception:  # no session bus (a server, a container)
        return False
    ready, ok = threading.Event(), []
    threading.Thread(target=_run, args=(Gio, GLib, on_open, on_quit, ready, ok), daemon=True, name="tray").start()
    ready.wait(START_SECONDS)
    return bool(ok)


def _run(Gio, GLib, on_open, on_quit, ready: threading.Event, ok: list) -> None:
    ctx = GLib.MainContext.new()
    ctx.push_thread_default()
    loop = GLib.MainLoop.new(ctx, False)
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    node = Gio.DBusNodeInfo.new_for_xml(_XML)
    size = ICON_PIXELS
    props = {
        "Category": GLib.Variant("s", "ApplicationStatus"), "Id": GLib.Variant("s", edition.WINDOW_CLASS),
        "Title": GLib.Variant("s", edition.NAME), "Status": GLib.Variant("s", "Active"),
        "IconName": GLib.Variant("s", ""), "IconPixmap": GLib.Variant("a(iiay)", [(size, size, icon(size))]),
        "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], edition.NAME, "Running. Click to open; the menu quits it.")),
        "ItemIsMenu": GLib.Variant("b", False), "Menu": GLib.Variant("o", "/MenuBar"),
        "Version": GLib.Variant("u", DBUSMENU_VERSION), "TextDirection": GLib.Variant("s", "ltr"),
    }

    def properties(menu_id: int) -> dict:
        return {key: GLib.Variant("s", value) for key, value in _MENU[menu_id].items()}

    def item(menu_id: int) -> GLib.Variant:
        return GLib.Variant("(ia{sv}av)", (menu_id, properties(menu_id), []))

    def act(what):  # the app's own work runs off the D-Bus thread
        threading.Thread(target=what, daemon=True).start()

    def clicked(menu_id: int) -> None:
        if menu_id == OPEN_ITEM:
            act(on_open)
        elif menu_id == QUIT_ITEM:
            act(on_quit)

    def method(conn, sender, path, iface, name, params, inv):
        if iface == _ITEM:
            if name in ("Activate", "SecondaryActivate"):
                act(on_open)
            inv.return_value(None)
            return
        if name == "GetLayout":
            root = GLib.Variant("(ia{sv}av)", (ROOT_ITEM, {"children-display": GLib.Variant("s", "submenu")},
                                                 [item(menu_id) for menu_id in _MENU]))
            inv.return_value(GLib.Variant.new_tuple(GLib.Variant("u", 1), root))
        elif name == "GetGroupProperties":
            ids = params.unpack()[0] or list(_MENU)
            inv.return_value(GLib.Variant("(a(ia{sv}))", ([(menu_id, properties(menu_id))
                                                            for menu_id in ids if menu_id in _MENU],)))
        elif name == "GetProperty":
            menu_id, prop = params.unpack()
            inv.return_value(GLib.Variant("(v)", (GLib.Variant("s", _MENU.get(menu_id, {}).get(prop, "")),)))
        elif name == "Event":
            menu_id, event = params.unpack()[:2]
            if event == "clicked":
                clicked(menu_id)
            inv.return_value(None)
        elif name == "EventGroup":
            for menu_id, event, _, _ in params.unpack()[0]:
                if event == "clicked":
                    clicked(menu_id)
            inv.return_value(GLib.Variant("(ai)", ([],)))
        elif name == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        elif name == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_value(None)

    def get_prop(conn, sender, path, iface, prop):
        if iface == "com.canonical.dbusmenu" and prop == "Status":
            return GLib.Variant("s", "normal")
        return props.get(prop)

    try:
        bus.register_object("/StatusNotifierItem", node.interfaces[0], method, get_prop, None)
        bus.register_object("/MenuBar", node.interfaces[1], method, get_prop, None)
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        Gio.bus_own_name_on_connection(bus, name, Gio.BusNameOwnerFlags.NONE, None, None)
        bus.call_sync(_WATCHER, "/StatusNotifierWatcher", _WATCHER, "RegisterStatusNotifierItem",
                      GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE, REGISTER_MS, None)
        ok.append(True)
    except Exception:  # a tray host that refuses the icon: the app goes on without one, as start() says
        logging.getLogger("decompdecomp").info("the tray icon couldn't be shown", exc_info=True)
        ready.set()
        return
    ready.set()
    loop.run()
