"""The app's own life: updates (updates.py) and the watcher that checks for them, the app window, quitting.
"""

from __future__ import annotations

import os
import threading
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import hostenv
from .. import updates, window
from . import App


def register(app: App) -> None:
    mcp, port, secret, manager, settings = app.mcp, app.port, app.secret, app.manager, app.settings
    guard, call, fail, updater = app.guard, app.call, app.fail, app.updater

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
                                 "job": updater.job, "runtime": hostenv.runtime()})
        return JSONResponse(await call(lambda: updater.status(refresh)))

    def checking() -> bool:
        return settings.data.get("check_updates", True) is not False and os.environ.get("DECOMP_UPDATES") != "off"

    def watch_updates() -> None:
        """Check now and then; with "install updates automatically" on, install one while no helper is working."""
        time.sleep(20)
        while True:
            if not checking():
                time.sleep(300)
                continue
            try:
                st = updater.status()
                busy = any(s.state in ("starting", "working") for s in manager.sessions.values())
                if st.get("available") and st.get("can_apply") and settings.data.get("auto_update") and not busy:
                    updater.apply()
            except (ValueError, OSError):
                pass
            time.sleep(300)

    @mcp.custom_route("/api/window", methods=["POST"])
    async def api_window(request: Request) -> Response:
        """`decomp` run again while the app is running: show its window (one window; a tab when there's no app browser)."""
        if (bad := guard(request, write=True)):
            return bad
        return JSONResponse({"window": window.open_app(f"http://127.0.0.1:{port}/?k={secret}")})

    @mcp.custom_route("/api/quit", methods=["POST"])
    async def api_quit(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad

        def bye():
            window.close_app()
            manager.shutdown()
            time.sleep(0.5)
            os._exit(0)
        threading.Thread(target=bye, daemon=True).start()
        return JSONResponse({"ok": True})

    threading.Thread(target=watch_updates, daemon=True, name="updates").start()
