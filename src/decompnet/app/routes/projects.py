"""Everything under one project: its overview and live feed, the function drawer and diffs, the compiler and its
ranges, the compiler map's survey, export, priorities, notes, suggestions and function-end reports, autopilot.
Each is fn(slug, request, data) wrapped by with_project, which checks the request and answers errors.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import consoles, hostenv, toolchains
from ...forks.project import SCOPES
from ...network import NetworkError
from .. import saves
from ..engine import parse_ranges
from . import App


def register(app: App) -> None:
    net, mcp, manager, jobs = app.net, app.mcp, app.manager, app.jobs
    settings, guard, body, call, fail = app.settings, app.guard, app.body, app.call, app.fail

    def with_project(fn):
        async def handler(request: Request) -> Response:
            if (bad := guard(request, write=request.method == "POST")):
                return bad
            data = await body(request) if request.method == "POST" else {}
            slug = data.get("slug") or request.query_params.get("slug", "")
            try:
                def perform():
                    result = fn(slug, request, data)
                    if request.method == "POST" and net.store.one("SELECT 1 FROM projects WHERE slug=?", slug):
                        net.progress.sync(slug, force=True)
                    return result
                return JSONResponse(await call(perform))
            except (NetworkError, ValueError, toolchains.ToolchainError, OSError) as e:
                return fail(e)
        return handler

    def p_overview(slug, request, data):
        settings.data["last_project"] = slug
        settings.save()
        overview = net.overview(slug)
        net.warm_briefs_soon(slug)  # the project is open: agents' first briefs come next
        return overview

    def p_live(slug, request, data):
        q = request.query_params
        out = net.live(slug, int(q.get("e", 0) or 0), int(q.get("a", 0) or 0))
        try:
            since = json.loads(q.get("s") or "{}")
        except ValueError:
            since = {}
        out["sessions"] = [s for s in manager.snapshot(since) if s["slug"] == slug]
        return out

    def p_compiler(slug, request, data):
        if net.fork_slug(slug) and "auto" not in data:  # its source files each have a compiler (DS)
            tc = str(data.get("toolchain", ""))
            if not str(data.get("ranges") or "").strip():  # the whole game: every file, one at a time
                return {"job": jobs.start("compiler", lambda log: net.forks.set_compiler_everywhere(slug, tc, log))}
            said = net.forks.set_compiler_ranges(slug, parse_ranges(str(data["ranges"])), None if data.get("remove") else tc)
            return net.overview(slug) | {"said": said}
        if "auto" in data:  # this project's suggestions apply by themselves once proven, or wait for the user
            net.set_auto_adjust(slug, bool(data["auto"]))
        elif str(data.get("ranges") or "").strip():  # these address ranges only (or back to the project's: remove)
            net.set_compiler_ranges(slug, parse_ranges(str(data["ranges"])),
                                    None if data.get("remove") else str(data.get("toolchain", "")), data.get("flags") or None,
                                    reopen=bool(data.get("reopen")))
        else:
            net.set_compiler(slug, str(data.get("toolchain", "")), data.get("flags") or None)
        return net.overview(slug)

    def p_export(slug, request, data):
        def work(log):
            res = net.export_rom(slug, log=log)
            return {k: v for k, v in res.items() if k != "log"} | {"log": res.get("log", "")[-1500:]}
        return {"job": jobs.start("export", work)}

    def p_reimport(slug, request, data):
        if not data.get("confirmed"):
            raise ValueError("confirm that this is your own legally obtained copy of the game")
        path = hostenv.local_path(str(data.get("path") or ""))
        if not path.is_file():
            raise ValueError("choose a game file to reimport")
        proj = net.project_row(slug)
        cfg = json.loads(proj["config"])
        if cfg.get("kind") != "rom":
            raise NetworkError("only ROM projects can be reimported")
        if slug in net.rooms or slug in net.links:
            raise NetworkError("leave the shared room before reimporting this game")
        if consoles.identify(path).id != net.console(proj).id:
            raise NetworkError("choose a game for the same console as this project")
        if net.surveys.get(slug, {}).get("state") == "running":
            raise NetworkError("wait for the compiler survey to finish before reimporting this game")
        manager.stop_project(slug)
        net.stop_autopilot(slug)
        net.twins.stop(slug)
        net.permuter.stop(slug)
        return {"job": jobs.start(
            "reimport", lambda log: net.reimport_rom(slug, path, confirmed=True, name=path.name, log=log))}

    def p_compilermap(slug, request, data):
        """The compiler answer (GET): which compiler built the game, and its parts; the app checks by itself when the
        answer is old. POST: check again now (the survey and the applied ranges)."""
        return net.compiler_answer(slug, check_now=request.method == "POST")

    def p_open(slug, request, data):
        why = hostenv.open_folder(Path(net.project_row(slug)["root"]))
        return {"ok": why is None, "message": why}

    def p_delete(slug, request, data):
        manager.forget_project(slug)  # (it stopped every project's helpers)
        proj = net.project_row(slug)
        net.knowledge.forget(slug)
        net.remove_project(slug)
        if data.get("files"):
            shutil.rmtree(proj["root"], ignore_errors=True)
        if settings.data.get("last_project") == slug:
            settings.data["last_project"] = None
            settings.save()
        return {"ok": True}

    def p_try(slug, request, data):
        """You, editing a function in the GUI: judged like an agent's attempt (and credited to "you")."""
        agent, _ = net.local_agent("you")
        res = net.attempt(agent, str(data.get("name", "")), str(data.get("source", "")), slug)
        return {"score": res["score"], "matched": res["matched"], "text": res["text"][-4000:]}

    def p_release(slug, request, data):
        agent, _ = net.local_agent("you")
        try:
            net.release(agent, str(data.get("name", "")), "", slug)
        except NetworkError:
            pass  # not yours (any more): nothing to give back
        return {"ok": True}

    def p_recheck(slug, request, data):
        """Check again: the project looked at with what this version knows (Core.recheck), in the background."""
        return {"job": jobs.start("recheck", lambda log: {
            "said": net.recheck(slug, log=log) or "nothing to do on this computer, as the room's host checks the game"})}

    def p_set_aside(slug, request, data):
        """You set a function aside: no C makes it (Network.set_aside_by_you)."""
        net.set_aside_by_you(str(data.get("name", "")), slug, str(data.get("why", "")))
        return {"ok": True}

    def p_reopen(slug, request, data):
        """You give a set-aside function back to the agents."""
        net.reopen(str(data.get("name", "")), slug)
        return {"ok": True}

    def p_instructions(slug, request, data):
        net.set_instructions(slug, str(data.get("text", "")))
        return net.overview(slug)

    def p_title(slug, request, data):
        net.set_title(slug, str(data.get("title", "")))
        return net.overview(slug)

    def p_priority(slug, request, data):
        return {"priority": net.set_priority(slug, str(data.get("name", "")), bool(data.get("on", True)))}

    def p_scope(slug, request, data):
        """What the project covers ({scope: "game" | "program"}): kept in its manifest, then the project is measured
        again (a job: a big project takes a while), so its set-aside functions open to agents and count, or stop."""
        scope = str(data.get("scope", ""))
        if scope not in SCOPES:
            raise ValueError(f"a project covers the game's own code or the whole program, not {scope!r}")
        if not net.fork_slug(slug):
            raise ValueError("only projects started from scratch or forked have code set aside to choose for")

        def work(log):
            net.forks.set_scope(slug, scope, log=log)
            return net.overview(slug)
        return {"job": jobs.start("scope", work)}

    def p_unanalysed(slug, request, data):
        """Bring in the code the project's import left unanalysed (Console.unanalysed), once: a new analysis of that
        code alone (a job: it runs the console's analysis, Ghidra for some), then the project measured again."""
        if not net.fork_slug(slug):
            raise ValueError("only projects started from scratch have code a new analysis can bring in")

        def work(log):
            net.forks.analyse_unanalysed(slug, log=log)
            return net.overview(slug)
        return {"job": jobs.start("analysis", work)}

    def p_note(slug, request, data):
        """A note from you on a function (or the whole project): agents read notes when they claim it."""
        agent, _ = net.local_agent("you")
        text = str(data.get("text", "")).strip()
        if not text:
            raise ValueError("write the note first")
        return {"message": net.add_note(agent, slug, text[:2000], str(data.get("function", "")) or None)}

    def p_suggestion(slug, request, data):
        net.resolve_suggestion(slug, int(data.get("id", 0)), data.get("action") == "apply")
        return net.overview(slug)

    def p_boundary(slug, request, data):
        net.resolve_boundary(slug, int(data.get("id", 0)), data.get("action") == "apply")
        return net.overview(slug)

    def p_autopilot(slug, request, data):
        if data.get("action") == "stop":
            net.stop_autopilot(slug)
        else:
            net.start_autopilot(slug)
        return net.overview(slug)

    def p_save(slug, request, data):
        """Save the project's work as plain files (app/saves.py): to its progress/ folder, or one the user picks."""
        return saves.save(net, slug, hostenv.local_path(data["folder"]) if data.get("folder") else None)

    def p_knowledge(slug, request, data):
        """What agents learned: GET lists it; POST accepts or rejects one ({id, accept}), or sets whether agreed
        knowledge is added by itself ({auto})."""
        if request.method == "POST":
            if "auto" in data:
                net.knowledge.set_auto(slug, bool(data["auto"]))
            else:
                net.knowledge.decide(slug, int(data.get("id") or 0), bool(data.get("accept")))
        return net.knowledge.listing(slug)

    routes = {
        "/api/project": (p_overview, ["GET"]),
        "/api/knowledge": (p_knowledge, ["GET", "POST"]),
        "/api/modelcosts": (lambda s, r, d: manager.costs(s), ["GET"]),
        "/api/save": (p_save, ["POST"]),
        "/api/live": (p_live, ["GET"]),
        "/api/function": (lambda s, r, d: net.function_detail(s, r.query_params.get("name", "")), ["GET"]),
        "/api/diff": (lambda s, r, d: net.function_diff(s, r.query_params.get("name", ""),
                                                        r.query_params.get("which", "latest")), ["GET"]),
        "/api/compiler": (p_compiler, ["POST"]), "/api/export": (p_export, ["POST"]), "/api/open": (p_open, ["POST"]),
        "/api/reimport": (p_reimport, ["POST"]),
        "/api/delete": (p_delete, ["POST"]),
        "/api/try": (p_try, ["POST"]), "/api/release": (p_release, ["POST"]), "/api/reopen": (p_reopen, ["POST"]), "/api/setaside": (p_set_aside, ["POST"]),
        "/api/recheck": (p_recheck, ["POST"]),
        "/api/autopilot": (p_autopilot, ["POST"]), "/api/instructions": (p_instructions, ["POST"]), "/api/title": (p_title, ["POST"]),
        "/api/priority": (p_priority, ["POST"]), "/api/note": (p_note, ["POST"]), "/api/scope": (p_scope, ["POST"]),
        "/api/unanalysed": (p_unanalysed, ["POST"]),
        "/api/suggestion": (p_suggestion, ["POST"]), "/api/boundary": (p_boundary, ["POST"]),
        "/api/stats": (lambda s, r, d: net.stats(s), ["GET"]),
        "/api/compilermap": (p_compilermap, ["GET", "POST"]),
    }
    for path, (fn, methods) in routes.items():
        mcp.custom_route(path, methods=methods)(with_project(fn))

    app.with_project = with_project
