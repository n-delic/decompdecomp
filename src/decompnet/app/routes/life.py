"""The app's own life: updates (updates.py) and the watcher that checks for them, the window, quitting.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import updates, window
from . import App

page_log = logging.getLogger("decompdecomp.page")
PAGE_ERROR_CHARS, PAGE_STACK_CHARS = 500, 4000  # what one page error may write to the log


FIRST_CHECK_SECONDS = 20  # after the app starts: its own start-up comes first
CHECK_EVERY_SECONDS = 300  # then whether a newer version is out, every five minutes


def register(app: App) -> None:
    mcp, manager, settings = app.mcp, app.manager, app.settings
    guard, body, call, fail, updater = app.guard, app.body, app.call, app.fail, app.updater

    @mcp.custom_route("/api/update", methods=["GET", "POST"])
    async def api_update(request: Request) -> Response:
        """Whether a newer decompdecomp is out (checked now and then; ?refresh=1 now) and, POSTed, installing it."""
        if (bad := guard(request, write=request.method == "POST")):
            return bad
        if request.method == "POST":
            try:
                await call(updater.apply)
            except ValueError as e:
                return fail(e)
        refresh = request.query_params.get("refresh") == "1"
        if not refresh and not checking():  # the user turned checks off: only "Check now" asks
            return JSONResponse({"disabled": True, "available": False, "current": updates.installed(),
                                 "job": updater.job})
        return JSONResponse(await call(lambda: updater.status(refresh)))

    def checking() -> bool:
        return settings.data.get("check_updates", True) is not False and os.environ.get("DECOMP_UPDATES") != "off"

    def watch_updates() -> None:
        """Check now and then; with "install updates automatically" on, install one while no helper is working."""
        time.sleep(FIRST_CHECK_SECONDS)
        while True:
            if not checking():
                time.sleep(CHECK_EVERY_SECONDS)
                continue
            try:
                st = updater.status()
                busy = any(s.state in ("starting", "working") for s in manager.sessions.values())
                if st.get("available") and st.get("can_apply") and settings.data.get("auto_update") and not busy:
                    updater.apply()
            except (ValueError, OSError):
                pass
            time.sleep(CHECK_EVERY_SECONDS)

    @mcp.custom_route("/api/window", methods=["POST"])
    async def api_window(request: Request) -> Response:
        """`decomp` run again while the app runs, or the tray icon: open the window, or bring it to the front."""
        if (bad := guard(request, write=True)):
            return bad
        return JSONResponse({"window": await call(window.open_app)})

    @mcp.custom_route("/api/close", methods=["GET", "POST"])
    async def api_close(request: Request) -> Response:
        """The window is being closed. GET: what's still working (the page asks the user then). POST: keep it working
        without the window ({"keep": true}: the tray icon or `decomp` brings it back), or quit."""
        if (bad := guard(request, write=request.method == "POST")):
            return bad
        if request.method == "GET":
            return JSONResponse({"helpers": len(manager.running()), "busy": app.busy(), "tray": window.IN_TRAY})
        if (await body(request)).get("keep"):
            await call(window.close_app)
            return JSONResponse({"ok": True})
        threading.Thread(target=app.quit_app, daemon=True).start()
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/log", methods=["POST"])
    async def api_log(request: Request) -> Response:
        """An error in the page's own scripts, into the app's log: a broken page otherwise leaves nothing behind."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        page_log.warning("page: %s (%s)\n%s", str(data.get("message", ""))[:PAGE_ERROR_CHARS],
                         str(data.get("where", ""))[:PAGE_ERROR_CHARS], str(data.get("stack", ""))[:PAGE_STACK_CHARS])
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/quit", methods=["POST"])
    async def api_quit(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        threading.Thread(target=app.quit_app, daemon=True).start()
        return JSONResponse({"ok": True})

    threading.Thread(target=watch_updates, daemon=True, name="updates").start()
