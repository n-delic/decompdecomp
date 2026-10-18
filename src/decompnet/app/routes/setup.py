"""Compilers and tools (Setup), agents: installing them, starting and stopping helpers, connecting an agent the
user runs (MCP), their sign-in tokens, and the settings.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import hostenv, toolchains
from ...network import NetworkError
from .. import agents, connect
from ..support import _session_options, _spread
from . import App


def register(app: App) -> None:
    net, mcp, mcp_url, manager, jobs = app.net, app.mcp, app.mcp_url, app.manager, app.jobs
    settings, guard, body, fail = app.settings, app.guard, app.body, app.fail

    @mcp.custom_route("/api/toolchain", methods=["POST"])
    async def api_toolchain(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        tid, accept = str(data.get("id", "")), bool(data.get("accept_license"))
        return JSONResponse({"job": jobs.start("toolchain", lambda log: (toolchains.install(tid, accept_license=accept, log=log)
                                                                         and {"id": tid}))})

    @mcp.custom_route("/api/agents/install", methods=["POST"])
    async def api_agent_install(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        kind = agents.KINDS.get(agents.kind_id(str((await body(request)).get("kind", ""))))
        if kind is None or not kind.install:
            return fail(ValueError("nothing to install"))
        cmd = kind.install["windows" if sys.platform == "win32" else "unix"]
        env = None
        if sys.platform == "win32":
            env = dict(os.environ, PATH=agents.search_path())  # Node.js installed since the app started counts
            if cmd.startswith("npm ") and not shutil.which("npm", path=env["PATH"]):
                return fail(ValueError(f"{kind.name} is installed with npm: install Node.js from https://nodejs.org "
                                       "first (the LTS version), then try again"))

        def work(log):
            log(f"$ {cmd}")
            if sys.platform == "win32":
                # Node.js's npm is a PowerShell script (npm.ps1) that Windows' default execution policy won't run;
                # output as UTF-8 so it reads right here; never wait at a prompt nobody sees
                argv = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                        f"[Console]::OutputEncoding = [Text.Encoding]::UTF8; {cmd}"]
            else:
                argv = ["bash", "-lc", cmd]
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                    errors="replace", env=env)
            for line in proc.stdout:
                log(line.rstrip())
            if proc.wait() != 0:
                raise RuntimeError(f"the installer exited with code {proc.returncode}")
            agents._versions.pop(kind.id, None)
            return {"kind": kind.id}
        return JSONResponse({"job": jobs.start("agent-install", work)})

    @mcp.custom_route("/api/agents/start", methods=["POST"])
    async def api_agents_start(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        kind, slug = str(data.get("kind", "")), str(data.get("slug", ""))
        given = data.get("options") or {}
        options = {k: v for k, v in given.items() if k in ("model", "per_session")}
        options.setdefault("per_session", settings.data.get("per_session", agents.PER_SESSION))
        count = max(1, min(int(data.get("count", 1)), 8))
        picked: list[str] = []
        try:
            net.project_row(slug)
            net.warm_briefs_soon(slug)  # ready before the helpers' first claims
            if kind == "api":
                picked = [str(e) for e in (given.get("endpoints") or ([given["endpoint"]] if given.get("endpoint") else []))]
                plan = _spread(settings, manager, picked, count, given)
                ids = [sid for entry, n in plan for sid in manager.start(kind, slug, n, options | _session_options(entry))]
            else:
                ids = manager.start(kind, slug, count, options)
        except (NetworkError, ValueError) as e:
            return fail(e)
        # remembered, so the next start is one click; models typed in come back as suggestions
        model = str(options.get("model") or "")
        settings.data["launcher"] = {"kind": kind, "model": model, "count": count} | ({"endpoints": picked} if picked else {})
        if model and kind != "api":
            recent = settings.data.setdefault("recent_models", {}).setdefault(kind, [])
            settings.data["recent_models"][kind] = [model] + [m for m in recent if m != model][:7]
        settings.save()
        return JSONResponse({"started": ids})

    @mcp.custom_route("/api/connect", methods=["GET", "POST"])
    async def api_connect(request: Request) -> Response:
        """Your agents (Claude Code, Codex, ...) with decompdecomp's tools: whether they have them, and adding them."""
        if (bad := guard(request, write=request.method == "POST")):
            return bad
        if request.method == "POST":
            kind = str((await body(request)).get("id", ""))
            try:
                msg = await anyio.to_thread.run_sync(lambda: connect.connect(kind))
            except (ValueError, OSError, subprocess.TimeoutExpired) as e:
                return fail(e)
            return JSONResponse({"ok": True, "message": msg})

        def rows():
            from concurrent.futures import ThreadPoolExecutor

            found = [a for a in agents.detect() if a["id"] in connect.LABEL]
            with ThreadPoolExecutor(len(found) or 1) as pool:  # each agent's own CLI answers; ask them all at once
                linked = list(pool.map(lambda a: a["installed"] and connect.status(a["id"]), found))
            return [{"id": a["id"], "name": a["name"], "installed": a["installed"], "connected": ok,
                     "command": connect.shown(a["id"])} for a, ok in zip(found, linked)]
        if (host := hostenv.host_bridge()) is not None:  # the user's agents live outside decompdecomp's Linux
            return JSONResponse({"agents": [], "bridge": host, "host_bridge": host, **hostenv.info()})
        return JSONResponse({"agents": await anyio.to_thread.run_sync(rows), "bridge": connect.bridge_command()})

    @mcp.custom_route("/api/agents/token", methods=["POST"])
    async def api_agents_token(request: Request) -> Response:
        """An identity for an agent the user talks to, connecting through `decomp mcp`."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            agent, token = net.chat_agent(str(data.get("name") or "chat"))
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"agent": agent.name, "token": token, "url": mcp_url})

    @mcp.custom_route("/api/agents/stop", methods=["POST"])
    async def api_agents_stop(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if data.get("remove"):
            manager.remove(str(data.get("id")))
        elif data.get("slug"):  # every helper on one project
            for s in list(manager.sessions.values()):
                if s.slug == data["slug"] and s.state in ("starting", "working", "waiting"):
                    manager.stop(s.id)
        else:
            manager.stop(data.get("id") or None)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/settings", methods=["POST"])
    async def api_settings(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if isinstance(data.get("api"), dict):  # (before 0.10.1: the one endpoint there was) the first one
            first = settings.endpoints()[0]["id"] if settings.endpoints() else ""
            settings.save_endpoint(data["api"] | {"id": first})
        if isinstance(data.get("endpoint"), dict):
            settings.save_endpoint(data["endpoint"])
        if data.get("remove_endpoint"):
            settings.remove_endpoint(str(data["remove_endpoint"]))
            for sid, sess in list(manager.sessions.items()):  # its sessions stop with it
                if sess.kind == "api" and sess.options.get("endpoint") == str(data["remove_endpoint"]):
                    manager.stop(sid)
        if "per_session" in data:
            settings.data["per_session"] = max(1, min(int(data["per_session"]), 20))
        if "autopilot" in data:
            settings.data["autopilot"] = bool(data["autopilot"])
        if "budget_usd" in data:
            settings.data["budget_usd"] = max(0.0, float(data["budget_usd"] or 0))
        if "auto_update" in data:
            settings.data["auto_update"] = bool(data["auto_update"])
        if "check_updates" in data:
            settings.data["check_updates"] = bool(data["check_updates"])
        if "auto_compiler" in data:
            settings.data["auto_compiler"] = net.auto_compiler = bool(data["auto_compiler"])
        settings.save()
        return JSONResponse(settings.public())
