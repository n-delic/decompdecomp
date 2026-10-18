"""The app as a whole: its state (versions, projects, compilers, agents, settings), the project list, importing a
game, the function map (every function, sent only when it changed), the log and background jobs.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import __version__, hostenv, toolchains
from ...network import NetworkError
from .. import agents, updates
from ..engine import COMPILERS, MW_VERSIONS, PLATFORM_STATUS, SHORT_NAMES, TOOLS
from ..support import MAX_DISC, _static, job_error
from . import App


def register(app: App) -> None:
    net, mcp, home, mcp_url, build_id = app.net, app.mcp, app.home, app.mcp_url, app.build_id
    manager, jobs, settings, guard, call = app.manager, app.jobs, app.settings, app.guard, app.call
    fail = app.fail

    def overviews() -> list[tuple[str, dict | None, str]]:
        """(slug, overview or None, why not) for every project. One that can't be read is still listed, with the
        reason: before, one odd project emptied the whole sidebar, or quietly went missing from it."""
        out = []
        for p in net.store.all("SELECT slug, name FROM projects ORDER BY id DESC"):
            try:
                out.append((p["slug"], net.overview(p["slug"]), ""))
            except Exception as e:  # noqa: BLE001
                jobs.logger.warning("project %s can't be read: %s", p["slug"], e, exc_info=not isinstance(e, NetworkError))
                out.append((p["slug"], {"slug": p["slug"], "title": p["name"] or p["slug"]}, job_error(e, jobs.log_path)))
        return out

    def state() -> dict:
        projects = [ov | ({"broken": why} if why else {}) for _, ov, why in overviews()]
        tcs = [t for t in toolchains.status() if t["id"] in COMPILERS or t["id"] in TOOLS]
        for t in tcs:
            t["use"] = COMPILERS.get(t["id"]) or TOOLS[t["id"]]
            t["name"] = SHORT_NAMES.get(t["id"], t["name"])
        return {"version": __version__, "build": build_id, "install": updates.installed(),
                "platform": toolchains.current_platform(), "home": hostenv.shown_path(home.parent),
                "log_file": hostenv.shown_path(home / "logs" / "decomp.log"),
                "projects": projects, "toolchains": tcs, "agents": manager.detected(),
                "agents_detecting": manager.detecting(),
                "api_presets": agents.API_PRESETS, "settings": settings.public(), "mcp_url": mcp_url,
                "platform_status": PLATFORM_STATUS,
                "codewarrior": {"installed": toolchains.installed("mwcc") is not None,
                                "ds_installed": toolchains.installed("mwccarm") is not None,
                                "msvc_installed": toolchains.installed("msvc") is not None, **MW_VERSIONS}}

    def autopilot(slug: str) -> None:
        """Free matches first, in the background, when the user wants that (the default): a game started from
        scratch, on any platform, as soon as it's ready. A public decomp, or one not set up yet, says no."""
        if settings.data.get("autopilot", True):
            try:
                net.start_autopilot(slug)
            except NetworkError:
                pass

    def project_list() -> list[dict]:
        """The sidebar: every project with its progress and how many helper agents work on it right now."""
        running: dict[str, int] = {}
        for s in manager.sessions.values():
            if s.state in ("starting", "working", "waiting"):
                running[s.slug] = running.get(s.slug, 0) + 1
        out = []
        for slug, ov, why in overviews():
            out.append({k: ov.get(k) for k in ("slug", "title", "kind", "origin", "platform", "platform_name", "ready",
                                                "functions", "matched", "bytes", "matched_bytes", "last_activity")}
                       | {"working": running.get(slug, 0), "room": (ov.get("room") or {}).get("role"),
                          "autopilot": (ov.get("autopilot") or {}).get("state")} | ({"broken": why} if why else {}))
        return out

    @mcp.custom_route("/api/projects", methods=["GET"])
    async def api_projects(request: Request) -> Response:
        return guard(request) or JSONResponse(await call(project_list))

    @mcp.custom_route("/api/state", methods=["GET"])
    async def api_state(request: Request) -> Response:
        return guard(request) or JSONResponse(await call(state))

    @mcp.custom_route("/api/import", methods=["POST"])
    async def api_import(request: Request) -> Response:
        """A game the user owns: the demo, an upload streamed to disk (discs are gigabytes), or a local path."""
        if (bad := guard(request, write=True)):
            return bad
        q = request.query_params
        toolchain = q.get("toolchain") or None
        if q.get("demo") == "1":
            game: bytes | Path | None = _static("demo/demo-quest-64.z64")
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

    MAP_KEYS = ("name", "vram", "size", "insns", "unit", "status", "best", "attempts", "agent", "matched_at", "matched_by",
                "cc", "ccx")

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
        raw = json.dumps(data, separators=(",", ":")).encode()
        if len(raw) > 4096 and "gzip" in request.headers.get("accept-encoding", ""):
            import gzip
            return Response(gzip.compress(raw, 1), media_type="application/json",
                            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        return Response(raw, media_type="application/json")

    @mcp.custom_route("/api/log", methods=["GET"])
    async def api_log(request: Request) -> Response:
        """The end of decompdecomp's log, to read or attach to a bug report (the app's data folder isn't always at
        hand: in Docker it's inside the container)."""
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
