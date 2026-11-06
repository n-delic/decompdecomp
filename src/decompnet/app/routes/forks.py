"""Public decomps forked by their repository address, and the projects started from scratch on the same build
(GameCube, Wii, PC): the project's AI policy, the clone, the game files, setup and its checks.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import hostenv
from ...forks.local import MAX_REPOSITORIES
from ...network import NetworkError
from .. import setup_fix
from ..support import MAX_DISC
from . import App


def register(app: App) -> None:
    net, mcp, jobs, settings, guard = app.net, app.mcp, app.jobs, app.settings, app.guard
    body, call, fail, autopilot, with_project = app.body, app.call, app.fail, app.autopilot, app.with_project

    @mcp.custom_route("/api/fork/policy", methods=["POST"])
    async def api_fork_policy(request: Request) -> Response:
        """What a public decomp says about AI and contributions, read from its own files before it's forked."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            return JSONResponse(await call(lambda: net.forks.policy(str(data.get("repo", "")))))
        except NetworkError as e:
            return fail(e)

    @mcp.custom_route("/api/fork", methods=["POST"])
    async def api_fork(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)

        def work(log):
            slug = net.forks.clone(str(data.get("repo", "")), name=str(data.get("name") or ""),
                                   platform=str(data.get("platform") or ""), version=data.get("version") or None, log=log)
            settings.data["last_project"] = slug
            settings.save()
            return {"slug": slug}
        return JSONResponse({"job": jobs.start("fork", work)})

    @mcp.custom_route("/api/fork/local/scan", methods=["POST"])
    async def api_local_scan(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        folder = str(data.get("folder") or "").strip()
        if not folder:
            return fail(ValueError("Choose a folder to scan."))
        def work(log):
            log("Looking for checked-out repositories…")
            result = net.forks.scan_local(hostenv.local_path(folder))
            log(f"Found {len(result['projects'])} repositories.")
            return result
        return JSONResponse({"job": jobs.start("local-scan", work)})

    @mcp.custom_route("/api/fork/local/import", methods=["POST"])
    async def api_local_import(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        paths = data.get("paths")
        folder = str(data.get("folder") or "").strip()
        if not folder or not isinstance(paths, list) or not paths or len(paths) > MAX_REPOSITORIES or any(
                not isinstance(path, str) or not path.strip() for path in paths):
            return fail(ValueError("Choose the folder and at least one repository from its scan."))
        def work(log):
            return net.forks.import_local(hostenv.local_path(folder), paths, copies=data.get("copies") is not False, log=log)
        return JSONResponse({"job": jobs.start("local-import", work)})

    def fork_of(slug: str):
        if not net.fork_slug(slug):
            raise NetworkError(f"{slug} isn't a fork")
        return net.project_row(slug)

    @mcp.custom_route("/api/fork/game", methods=["POST"])
    async def api_fork_game(request: Request) -> Response:
        """The user's game file, streamed to disk (GameCube and Wii discs are gigabytes)."""
        if (bad := guard(request, write=True)):
            return bad
        q = request.query_params
        slug = q.get("slug", "")
        if q.get("confirmed") != "1":
            return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
        try:
            proj = await app.read(fork_of, slug)
        except NetworkError as e:
            return fail(e)
        tmp = Path(proj["root"]) / ".decomp-wtf" / f"upload-{uuid.uuid4().hex[:8]}.tmp"
        await app.call(lambda: tmp.parent.mkdir(parents=True, exist_ok=True))
        size = 0
        async with await anyio.open_file(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_DISC:
                    await f.aclose()
                    await app.call(lambda: tmp.unlink(missing_ok=True))
                    return fail(ValueError("that file is larger than any game disc"))
                await f.write(chunk)
        name = Path(q.get("name") or "game.iso").name

        def work(log):
            try:
                res = net.forks.setup(slug, tmp, name, log=log, move=True)
            finally:
                tmp.unlink(missing_ok=True)
            active = res.get("slug", slug)
            settings.data["last_project"] = active
            settings.save()
            autopilot(active)
            return res
        return JSONResponse({"job": jobs.start("fork-setup", work)})

    @mcp.custom_route("/api/fork/find-rom", methods=["POST"])
    async def api_fork_find_rom(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug = str(data.get("slug", ""))
        try:
            await app.read(fork_of, slug)
        except NetworkError as e:
            return fail(e)
        def work(log):
            from ..romlibrary import find

            _, project, _ = net.forks.get(slug)
            return find(project, net.forks.needs(slug), str(settings.data.get("rom_library") or ""), log)
        return JSONResponse({"job": jobs.start("find-rom", work)})

    @mcp.custom_route("/api/fork/setup", methods=["POST"])
    async def api_fork_setup(request: Request) -> Response:
        """Set up with a game file already on this computer (`path`), or with none (`path` empty: run the build)."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug = str(data.get("slug", ""))
        try:
            await app.read(fork_of, slug)
        except NetworkError as e:
            return fail(e)
        path = str(data.get("path") or "").strip().strip('"')
        if path:
            if not data.get("confirmed"):
                return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
            src = Path(path).expanduser()
            if not await app.call(lambda: src.is_file()):
                return fail(ValueError(f"no file at {src}"))

        def work(log):
            res = net.forks.setup(slug, src, src.name, log=log) if path else net.forks.setup(slug, None, log=log)
            active = res.get("slug", slug)
            settings.data["last_project"] = active
            settings.save()
            autopilot(active)
            return res
        return JSONResponse({"job": jobs.start("fork-setup", work)})

    @mcp.custom_route("/api/fork/check", methods=["POST"])
    async def api_fork_check(request: Request) -> Response:
        """Pick a hand-built project up (or re-measure one): builds what changed, then reads its objdiff.json and
        progress."""
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        try:
            await app.read(fork_of, slug)
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"job": jobs.start("fork-check", lambda log: net.forks.check(slug, log=log, build=True))})

    @mcp.custom_route("/api/fork/fix-setup", methods=["GET", "POST"])
    async def api_fork_fix_setup(request: Request) -> Response:
        """A coding CLI, in a terminal in the project folder, to get a failed hand-built project to build."""
        if (bad := guard(request, write=request.method == "POST")):
            return bad
        if request.method == "GET":
            return JSONResponse({"agents": await call(setup_fix.choices)})
        data = await body(request)
        kind, slug = str(data.get("agent") or ""), str(data.get("slug", ""))
        try:
            proj = await app.read(fork_of, slug)
            if not any(a["id"] == kind and a["installed"] for a in await call(setup_fix.choices)):
                raise NetworkError("Choose an installed coding CLI.")
            folder = Path(proj["root"])
        except (NetworkError, ValueError) as e:
            return fail(e)
        return JSONResponse({"job": jobs.start("fix-setup", lambda log: setup_fix.launch(
            app.home, folder, str(data.get("error") or ""), kind, log))})

    @mcp.custom_route("/api/fork/rom-check", methods=["POST"])
    async def api_fork_rom_check(request: Request) -> Response:
        """A GameCube or Wii game from scratch: rebuild the program and compare it with the user's copy."""
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        try:
            await app.read(fork_of, slug)
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"job": jobs.start("rom-check", lambda log: net.forks.check_rom(slug, log=log))})

    @mcp.custom_route("/api/fork/start-files", methods=["POST"])
    async def api_fork_start_files(request: Request) -> Response:
        """Give the code no source file covers source files (dtk-template projects), verified by the game's checksum."""
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        try:
            await app.read(fork_of, slug)
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"job": jobs.start("fork-start-files", lambda log: net.forks.start_files(slug, log=log))})

    mcp.custom_route("/api/fork/needs", methods=["GET"])(with_project(lambda s, r, d: net.forks.needs(fork_of(s)["slug"])))
