"""Public decomps forked by their repository address, and the projects started from scratch on the same build
(GameCube, Wii, PC): the project's AI policy, the clone, the game files, setup and its checks.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...network import NetworkError
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
            proj = fork_of(slug)
        except NetworkError as e:
            return fail(e)
        tmp = Path(proj["root"]) / ".decomp-wtf" / f"upload-{uuid.uuid4().hex[:8]}.tmp"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_DISC:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    return fail(ValueError("that file is larger than any game disc"))
                f.write(chunk)
        name = Path(q.get("name") or "game.iso").name

        def work(log):
            try:
                res = net.forks.setup(slug, tmp, name, log=log, move=True)
            finally:
                tmp.unlink(missing_ok=True)
            autopilot(slug)
            return res
        return JSONResponse({"job": jobs.start("fork-setup", work)})

    @mcp.custom_route("/api/fork/setup", methods=["POST"])
    async def api_fork_setup(request: Request) -> Response:
        """Set up with a game file already on this computer (`path`), or with none (`path` empty: run the build)."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug = str(data.get("slug", ""))
        try:
            fork_of(slug)
        except NetworkError as e:
            return fail(e)
        path = str(data.get("path") or "").strip().strip('"')
        if path:
            if not data.get("confirmed"):
                return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
            src = Path(path).expanduser()
            if not src.is_file():
                return fail(ValueError(f"no file at {src}"))

        def work(log):
            res = net.forks.setup(slug, src, src.name, log=log) if path else net.forks.setup(slug, None, log=log)
            autopilot(slug)
            return res
        return JSONResponse({"job": jobs.start("fork-setup", work)})

    @mcp.custom_route("/api/fork/check", methods=["POST"])
    async def api_fork_check(request: Request) -> Response:
        """Pick a hand-built project up (or re-measure one): reads its objdiff.json and progress."""
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        try:
            fork_of(slug)
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"job": jobs.start("fork-check", lambda log: net.forks.check(slug, log=log))})

    @mcp.custom_route("/api/fork/rom-check", methods=["POST"])
    async def api_fork_rom_check(request: Request) -> Response:
        """A GameCube or Wii game from scratch: rebuild the program and compare it with the user's copy."""
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        try:
            fork_of(slug)
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
            fork_of(slug)
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"job": jobs.start("fork-start-files", lambda log: net.forks.start_files(slug, log=log))})

    mcp.custom_route("/api/fork/needs", methods=["GET"])(with_project(lambda s, r, d: net.forks.needs(fork_of(s)["slug"])))
