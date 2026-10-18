"""Decompiling together (collab.py): sharing a project as a room (over a tunnel, the LAN or Tailscale), managing
its members, joining a friend's room, reconnecting and leaving.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import hostenv
from ...network import NetworkError
from .. import collab
from ..support import MAX_DISC
from . import App


def register(app: App) -> None:
    net, mcp, home, manager = app.net, app.mcp, app.home, app.manager
    jobs, settings, guard, body, call = app.jobs, app.settings, app.guard, app.body, app.call
    fail = app.fail

    # The tunnel only ever reaches this separate little server, never the GUI or the MCP endpoint above.

    share = {"servers": {}, "tunnel": None, "links": {}}

    def collab_server(public_port: int | None = None) -> int:
        """The room server: on 127.0.0.1 behind a quick tunnel, or on every network interface at `public_port` for
        a room shared at an address the host controls (LAN, Tailscale, a server). Only the room's API is served
        there, never the GUI or the agents' endpoint."""
        import socket

        import uvicorn

        key = ("0.0.0.0", public_port) if public_port else ("127.0.0.1", None)
        if key in share["servers"]:
            return share["servers"][key][1]
        host = key[0]
        if public_port:
            port = public_port
        else:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(collab.host_app(net, net.rooms), host=host, port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True, name=f"collab-{port}").start()
        for _ in range(100):
            if server.started:
                break
            if getattr(server, "should_exit", False):
                break
            time.sleep(0.05)
        if not server.started:
            raise NetworkError(f"couldn't open port {port} for the room (is something else using it?)")
        share["servers"][key] = (server, port)
        return port

    def open_room(slug: str, mode: str, address: str = "", port: int = 0, log=print, keep: bool = False) -> str:
        room = net.rooms.get(slug) or collab.Room(net, slug)
        if mode == "address":
            address = address.strip().rstrip("/")
            if not re.match(r"^https?://[^/\s]+$", address):
                raise NetworkError("the address teammates use, like http://192.168.1.20:6481 or https://decomp.example.org")
            collab_server(port or 6481)
            code = room.open_invite(keep=keep, mode="address", address=address, port=port or 6481)
            base = address
        else:
            local = collab_server()
            code = room.open_invite(keep=keep, mode="tunnel")
            if share["tunnel"] is None or share["tunnel"].proc is None or share["tunnel"].proc.poll() is not None:
                log("opening a private tunnel to this computer (Cloudflare quick tunnel, no account needed)")
                share["tunnel"] = collab.Tunnel(local)
                share["tunnel"].start(log)
            base = share["tunnel"].url
        net.rooms[slug] = room
        link = f"{base}/join/{'open' if room.settings['visibility'] == 'public' else code}#room={slug}"
        share["links"][slug] = link
        return link

    # rooms shared at an address of the host's own come back when the app starts, with the same invite
    for p in net.store.all("SELECT slug, config FROM projects"):
        c = json.loads(p["config"]).get("collab") or {}
        if c.get("open") and c.get("mode") == "address" and c.get("address"):
            try:
                open_room(p["slug"], "address", c["address"], int(c.get("port") or 6481), keep=True)
            except (NetworkError, OSError):
                pass

    @mcp.custom_route("/api/collab", methods=["GET"])
    async def api_collab(request: Request) -> Response:
        if (bad := guard(request)):
            return bad
        slug = request.query_params.get("slug", "")
        try:
            info = await call(net._room_info, slug)
        except NetworkError as e:
            return fail(e)
        room = net.rooms.get(slug)
        cfg = (json.loads(net.project_row(slug)["config"]).get("collab") or {}) if slug else {}
        return JSONResponse({"sharing": room is not None, "link": share["links"].get(slug), "room": info,
                             "settings": room.settings if room else {"visibility": cfg.get("visibility", "private"),
                                                                       "approve": bool(cfg.get("approve"))},
                             "pending": room.pending() if room else [],
                             "mode": cfg.get("mode", "tunnel"), "address": cfg.get("address", ""), "port": cfg.get("port", 6481),
                             "was_sharing": bool(cfg.get("open")) and room is None,
                             "addresses": await anyio.to_thread.run_sync(collab.local_addresses),
                             "tunnel_supported": collab.cloudflared_available()})

    @mcp.custom_route("/api/collab/start", methods=["POST"])
    async def api_collab_start(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug = str(data.get("slug", ""))
        if slug in net.links:
            return fail(ValueError("this project belongs to a friend's room; only its host can invite people"))
        if net.fork_slug(slug) and not json.loads(net.project_row(slug)["config"]).get("ready"):
            return fail(ValueError("set the project up first (its Overview): then friends can join it"))
        mode = "address" if data.get("mode") == "address" else "tunnel"

        def work(log):
            room = net.rooms.get(slug) or collab.Room(net, slug)
            room.configure(data.get("visibility"), data.get("approve"))
            return {"link": open_room(slug, mode, str(data.get("address", "")), int(data.get("port") or 6481), log=log,
                                      keep=bool(data.get("keep")))}
        return JSONResponse({"job": jobs.start("share", work)})

    @mcp.custom_route("/api/collab/manage", methods=["POST"])
    async def api_collab_manage(request: Request) -> Response:
        """The host's controls: who's in, public or private, a fresh invite link."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug, action, name = str(data.get("slug", "")), str(data.get("action", "")), str(data.get("name", ""))
        room = net.rooms.get(slug)
        if room is None:
            return fail(ValueError("this project isn't being shared"))
        try:
            if action == "remove":
                room.remove(name)
            elif action in ("approve", "decline"):
                room.approve(name, action == "approve")
            elif action == "settings":
                room.configure(data.get("visibility"), data.get("approve"))
                c = room.cfg.get("collab") or {}
                share["links"][slug] = open_room(slug, c.get("mode", "tunnel"), c.get("address", ""),
                                                 int(c.get("port") or 6481), keep=True)
            elif action == "rotate":
                c = room.cfg.get("collab") or {}
                share["links"][slug] = open_room(slug, c.get("mode", "tunnel"), c.get("address", ""),
                                                 int(c.get("port") or 6481), keep=False)
            else:
                return fail(ValueError("unknown action"))
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"ok": True, "link": share["links"].get(slug), "members": room.members(),
                             "pending": room.pending(), "settings": room.settings})

    @mcp.custom_route("/api/collab/stop", methods=["POST"])
    async def api_collab_stop(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        room = net.rooms.pop(slug, None)
        if room is not None:
            room.close()
        share["links"].pop(slug, None)
        if not net.rooms and share["tunnel"] is not None:
            share["tunnel"].stop()
            share["tunnel"] = None
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/collab/join", methods=["POST"])
    async def api_collab_join(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        q = request.query_params
        if q.get("confirmed") != "1":
            return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
        link, name = q.get("link", ""), q.get("name", "")
        game: Path | None = None
        if q.get("path"):  # a file on this computer (a disc is gigabytes: not through the browser)
            game = hostenv.local_path(q["path"])
            if not game.is_file():
                return fail(ValueError(f"no file at {game}"))
        else:  # the browser's upload, streamed to disk
            upload = home / "uploads" / uuid.uuid4().hex[:8]
            upload.mkdir(parents=True, exist_ok=True)
            game = upload / (Path(q.get("filename") or "game.bin").name or "game.bin")
            size = 0
            with open(game, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_DISC:
                        f.close()
                        shutil.rmtree(upload, ignore_errors=True)
                        return fail(ValueError("that file is larger than any game disc"))
                    f.write(chunk)
            if size == 0:
                shutil.rmtree(upload, ignore_errors=True)
                game = None
        uploaded = game is not None and game.parent.parent == home / "uploads"

        def work(log):
            try:
                slug = collab.join_room(net, link, name, game or b"", log=log)
            finally:
                if uploaded:
                    shutil.rmtree(game.parent, ignore_errors=True)
            settings.data["last_project"] = slug
            settings.save()
            return {"slug": slug}
        return JSONResponse({"job": jobs.start("join", work)})

    @mcp.custom_route("/api/collab/reconnect", methods=["POST"])
    async def api_collab_reconnect(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug, link = str(data.get("slug", "")), str(data.get("link", ""))
        return JSONResponse({"job": jobs.start("reconnect", lambda log: {"slug": collab.reconnect(net, slug, link, log=log)})})

    @mcp.custom_route("/api/collab/leave", methods=["POST"])
    async def api_collab_leave(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        try:
            await call(net.unlink, str((await body(request)).get("slug", "")))
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"ok": True})

    def shutdown_rooms() -> None:
        if share["tunnel"] is not None:
            share["tunnel"].stop()
        for link in list(net.links.values()):
            link.stop()

    manager.on_stop = shutdown_rooms
