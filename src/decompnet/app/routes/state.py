"""The app as a whole: its state (versions, compilers, agents, settings), the project list, importing a
game, the function map (every function, sent only when it changed), the log and background jobs.
"""

from __future__ import annotations

import json
import platform
import shutil
import uuid
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import __version__, consoles, hostenv, toolchains
from ...agents.api import API_PRESETS
from ...network import NetworkError
from .. import cleanup, edition, picker, saves, updates
from ..engine import COMPILERS, TOOLS
from ..support import MAX_DISC, job_error, static_file
from . import App


def system_name() -> str:
    """The Linux distribution, as it names itself ("Ubuntu 24.04.1 LTS"), for bug reports: problems often depend on it
    (the window's WebKitGTK, the compilers' libraries)."""
    try:
        return platform.freedesktop_os_release().get("PRETTY_NAME", "")
    except OSError:
        return ""


def register(app: App) -> None:
    net, mcp, home, mcp_url, build_id = app.net, app.mcp, app.home, app.mcp_url, app.build_id
    manager, jobs, settings, guard, call, body = app.manager, app.jobs, app.settings, app.guard, app.call, app.body
    fail = app.fail

    def state() -> dict:
        tcs = [t for t in toolchains.status() if t["id"] in COMPILERS or t["id"] in TOOLS]
        for t in tcs:
            t["use"] = COMPILERS.get(t["id"]) or TOOLS[t["id"]]
            t["name"] = consoles.compiler_name(t["id"]) if t["id"] in COMPILERS else t["name"]
        return {"version": __version__, "build": build_id, "install": updates.installed(), "dev": edition.DEV,
                "platform": toolchains.current_platform(), "system": system_name(), "home": hostenv.shown_path(home.parent),
                "log_file": hostenv.shown_path(home / "logs" / "decomp.log"),
                "toolchains": tcs, "agents": manager.detected(), "agents_detecting": manager.detecting(),
                "bridges_removed": [] if settings.data.get("bridges_removed_seen") else cleanup.report(home),
                "api_presets": API_PRESETS, "settings": settings.public(), "mcp_url": mcp_url,
                # every console: names, status, compilers, layout, features (consoles/, Console.describe)
                "consoles": [c.describe() for c in consoles.CONSOLES]}

    def autopilot(slug: str) -> None:
        """Free matches first, in the background, when the user wants that (the default): a game started from
        scratch, on any platform, as soon as it's ready. A public decomp, or one not set up yet, says no."""
        if settings.data.get("autopilot", True):
            try:
                net.start_autopilot(slug)
            except NetworkError:
                pass

    def project_list() -> list[dict]:
        """The sidebar and the home page: every project with its progress and how many helper agents work on it
        right now. One that can't be read is still listed, with the reason: before, one odd project emptied the
        whole sidebar, or quietly went missing from it."""
        running: dict[str, int] = {}
        for s in manager.sessions.values():
            if s.state in ("starting", "working", "waiting"):
                running[s.slug] = running.get(s.slug, 0) + 1
        out = []
        for p in net.store.all("SELECT slug, name FROM projects ORDER BY id DESC"):
            try:
                out.append(net.summary(p["slug"]) | {"working": running.get(p["slug"], 0)})
            except Exception as e:  # noqa: BLE001
                jobs.logger.warning("project %s can't be read: %s", p["slug"], e, exc_info=not isinstance(e, NetworkError))
                out.append({"slug": p["slug"], "title": p["name"] or p["slug"], "working": 0,
                            "broken": job_error(e, jobs.log_path)})
        return out

    @mcp.custom_route("/api/projects", methods=["GET"])
    async def api_projects(request: Request) -> Response:
        return guard(request) or JSONResponse(await call(project_list))

    @mcp.custom_route("/api/state", methods=["GET"])
    async def api_state(request: Request) -> Response:
        return guard(request) or JSONResponse(await call(state))

    @mcp.custom_route("/api/restore", methods=["POST"])
    async def api_restore(request: Request) -> Response:
        """A project rebuilt from a save (a folder, or a git repository's address) and the user's own copy of the
        game (app/saves.py), in the background."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if not data.get("confirmed"):
            return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
        source, game = str(data.get("source") or "").strip(), hostenv.local_path(str(data.get("path") or ""))
        if not source or not game.is_file():
            return fail(ValueError("pick the saved project (a folder or a git address) and your copy of the game"))
        return JSONResponse({"job": jobs.start("restore", lambda log: saves.restore(net, source, game, log=log))})

    @mcp.custom_route("/api/pick", methods=["POST"])
    async def api_pick(request: Request) -> Response:
        """A folder or file the user picks in the system's own chooser (picker.py): "" if they cancelled."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            return JSONResponse({"path": await call(picker.choose, str(data.get("kind") or ""), str(data.get("title") or ""))})
        except ValueError as e:
            return fail(e)

    @mcp.custom_route("/api/events", methods=["GET"])
    async def api_events(request: Request) -> Response:
        """The notification centre, for every project: `after=N` what happened since event N (no `after`: where the
        feed starts now), `recent=1` the latest, newest first, for its history panel."""
        if (bad := guard(request)):
            return bad
        q = request.query_params
        if q.get("recent"):
            cleared = int(settings.data.get("notes_cleared_to") or 0)
            return JSONResponse({"events": [e for e in await call(net.recent_events) if e["id"] > cleared]})
        after = q.get("after", "")
        return JSONResponse(await call(net.events_after, int(after) if after.isdigit() else None))

    @mcp.custom_route("/api/import", methods=["POST"])
    async def api_import(request: Request) -> Response:
        """A game the user owns: the demo, an upload streamed to disk (discs are gigabytes), or a local path."""
        if (bad := guard(request, write=True)):
            return bad
        q = request.query_params
        toolchain = q.get("toolchain") or None
        if q.get("demo") == "1":
            game: bytes | Path | None = static_file("demo/demo-quest-64.z64")
            if game is None:
                return fail(ValueError("the demo ROM is missing from this install"))
            name = "demo-quest-64.z64"
        else:
            if q.get("confirmed") != "1":
                return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
            if q.get("path"):
                game = hostenv.local_path(q["path"])
                if not game.is_file():
                    return fail(ValueError(f"no file at {game}"))
                name = game.name
            else:
                name = Path(q.get("name") or "game.bin").name
                upload_dir = home / "uploads" / uuid.uuid4().hex[:8]  # keeps the file's own name
                upload_dir.mkdir(parents=True, exist_ok=True)
                game = upload_dir / name
                size = 0
                with open(game, "wb") as f:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_DISC:
                            f.close()
                            game.unlink(missing_ok=True)
                            return fail(ValueError("that file is larger than any game disc"))
                        f.write(chunk)
                if size == 0:
                    game.unlink(missing_ok=True)
                    return fail(ValueError("the file was empty"))
        uploaded = isinstance(game, Path) and game.parent.parent == home / "uploads"

        def work(log):
            try:
                slug = net.import_rom(game, toolchain=toolchain, confirmed=True, name=name, log=log)
            finally:
                if uploaded:
                    shutil.rmtree(game.parent, ignore_errors=True)
            settings.data["last_project"] = slug
            settings.save()
            autopilot(slug)
            return {"slug": slug}
        return JSONResponse({"job": jobs.start("import", work)})

    MAP_KEYS = ("name", "vram", "size", "insns", "unit", "status", "best", "attempts", "agent", "matched_at", "matched_by")
    COLUMN_KEYS = ("vram", "cc", "ccx")
    GZIP_FROM_BYTES = 4096  # smaller answers aren't worth compressing

    def packed(request: Request, data: dict) -> Response:
        raw = json.dumps(data, separators=(",", ":")).encode()
        if len(raw) > GZIP_FROM_BYTES and "gzip" in request.headers.get("accept-encoding", ""):
            import gzip
            return Response(gzip.compress(raw, 1), media_type="application/json",
                            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        return Response(raw, media_type="application/json")

    @mcp.custom_route("/api/map", methods=["GET"])
    async def api_map(request: Request) -> Response:
        """Every function, for the map and the table. `since`: the signature the GUI has; when nothing changed, the
        answer is just that (a big game's map is megabytes, and the GUI asks every few seconds). Rows go as arrays
        under `keys`, gzipped when the browser takes it."""
        if (bad := guard(request)):
            return bad
        slug = request.query_params.get("slug", "")

        def build() -> dict:
            sig = net.map_signature(slug)
            if request.query_params.get("since") == sig:
                return {"sig": sig, "same": True}
            rows = net.function_map(slug)
            return {"sig": sig, "keys": MAP_KEYS, "rows": [[r.get(k) for k in MAP_KEYS] for r in rows]}
        try:
            data = await call(build)
        except (NetworkError, ValueError) as e:
            return fail(e)
        return packed(request, data)

    @mcp.custom_route("/api/mapcolumns", methods=["GET"])
    async def api_map_columns(request: Request) -> Response:
        """The compiler map's colours for the ROM map (MIPS games), asked for after the map is drawn."""
        if (bad := guard(request)):
            return bad
        try:
            rows = await call(net.map_columns, request.query_params.get("slug", ""))
        except (NetworkError, ValueError) as e:
            return fail(e)
        return packed(request, {"keys": COLUMN_KEYS, "rows": rows})

    @mcp.custom_route("/api/log", methods=["GET"])
    async def api_log(request: Request) -> Response:
        """The end of decompdecomp's log, to read in the app or attach to a bug report."""
        if (bad := guard(request)):
            return bad
        path = home / "logs" / "decomp.log"
        try:
            with open(path, "rb") as f:
                f.seek(max(0, path.stat().st_size - (512 << 10)))
                text = f.read().decode("utf-8", "replace")
        except OSError:
            text = "(nothing logged yet)"
        return Response(text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": 'inline; filename="decomp.log"'})

    @mcp.custom_route("/api/job", methods=["GET"])
    async def api_job(request: Request) -> Response:
        if (bad := guard(request)):
            return bad
        job = jobs.jobs.get(request.query_params.get("id", ""))
        return JSONResponse(job) if job else fail(ValueError("no such job"), 404)

    app.autopilot = autopilot
