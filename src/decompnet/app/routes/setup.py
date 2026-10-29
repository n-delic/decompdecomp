"""Compilers and tools (Setup), agents: installing them, finding which are installed, starting, stopping and
restarting helpers, and the settings.
"""

from __future__ import annotations

import shutil
import subprocess

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from decompnet.hostenv import NO_WINDOW

from ... import agents as kinds
from ... import toolchains
from ...network import NetworkError
from .. import agents
from ..support import session_options, spread_sessions
from . import App

MAX_ENDPOINT_MODELS = 100  # an endpoint may list hundreds; the launcher shows this many at most


def register(app: App) -> None:
    net, mcp, manager, jobs = app.net, app.mcp, app.manager, app.jobs
    settings, guard, body, call, fail = app.settings, app.guard, app.body, app.call, app.fail

    @mcp.custom_route("/api/toolchain", methods=["POST"])
    async def api_toolchain(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        tid, accept = str(data.get("id", "")), bool(data.get("accept_license"))
        return JSONResponse({"job": jobs.start("toolchain", lambda log: (toolchains.install(tid, accept_license=accept, log=log)
                                                                         and {"id": tid}))})

    @mcp.custom_route("/api/toolchain/all", methods=["POST"])
    async def api_toolchain_all(request: Request) -> Response:
        """Every compiler of a project's console, then its compiler test again (Engine.install_compilers)."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug, accept = str(data.get("slug", "")), bool(data.get("accept_license"))
        return JSONResponse({"job": jobs.start("toolchain", lambda log: net.install_compilers(slug, accept, log=log))})

    @mcp.custom_route("/api/agents/install", methods=["POST"])
    async def api_agent_install(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        try:
            kind = kinds.get(str((await body(request)).get("kind", "")))
        except ValueError:
            kind = None
        if kind is None or not kind.install_text():
            return fail(ValueError("nothing to install"))
        if kind.install_text().startswith("npm ") and not shutil.which("npm"):
            return fail(ValueError(f"{kind.name} is installed with npm: install Node.js from https://nodejs.org "
                                   "first (the LTS version), then try again"))

        def work(log):
            log(f"$ {kind.install_text()}")
            proc = subprocess.Popen(kind.install_command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                                    creationflags=NO_WINDOW)
            for line in proc.stdout:
                log(line.rstrip())
            if proc.wait() != 0:
                raise RuntimeError(f"the installer exited with code {proc.returncode}")
            kinds.known_versions.pop(kind.id, None)
            manager.refresh_detected(wait=True)  # the launcher shows it, with its models, when the job is done
            return {"kind": kind.id}
        return JSONResponse({"job": jobs.start("agent-install", work)})

    @mcp.custom_route("/api/agents/detect", methods=["POST"])
    async def api_agents_detect(request: Request) -> Response:
        """Look again which agents are installed and their models (a sign-in since): in the background."""
        if (bad := guard(request, write=True)):
            return bad
        manager.refresh_detected()
        return JSONResponse({"detecting": True})

    @mcp.custom_route("/api/endpoints/models", methods=["GET"])
    async def api_endpoint_models(request: Request) -> Response:
        """List models from one saved endpoint, using its own key and never returning connection details."""
        if (bad := guard(request)):
            return bad
        entry = settings.endpoint(request.query_params.get("id", ""))
        if entry is None:
            return fail(ValueError("that API endpoint is no longer saved; choose it again"))
        headers = {"Authorization": f"Bearer {entry['api_key']}"} if entry.get("api_key") else {}
        try:
            response = await call(lambda: httpx.get(entry["base_url"].rstrip("/") + "/models", headers=headers,
                                                    timeout=10))
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                rows = []
            models = [{"id": str(row["id"]), "name": str(row.get("name") or row["id"])}
                      for row in rows if isinstance(row, dict) and row.get("id")]
            return JSONResponse({"models": models[:MAX_ENDPOINT_MODELS]})
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError):
            return JSONResponse({"models": [], "unavailable": True})

    @mcp.custom_route("/api/agents/start", methods=["POST"])
    async def api_agents_start(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        kind, slug = str(data.get("kind", "")), str(data.get("slug", ""))
        given = data.get("options") or {}
        requested_models = given.get("models", [])
        if not isinstance(requested_models, list) or any(
                not isinstance(model, (str, dict)) or
                (not model if isinstance(model, str) else
                 set(model) != {"endpoint", "model"} or
                 any(not isinstance(model.get(key), str) or not model[key] for key in ("endpoint", "model")))
                for model in requested_models):
            return fail(ValueError("models must be a list of model names"))
        seen_models = set()
        unique_models = []
        for model in requested_models:
            key = (model["endpoint"], model["model"]) if isinstance(model, dict) else model
            if key not in seen_models:
                seen_models.add(key)
                unique_models.append(model)
        requested_models = unique_models
        if given.get("multiple_models") and not requested_models:
            return fail(ValueError("choose at least one model"))
        options = {k: v for k, v in given.items() if k in ("model", "per_session", "task", "endpoints")}  # task: "readable" polishes
        options.setdefault("per_session", settings.data.get("per_session", agents.PER_SESSION))
        count = max(1, min(int(data.get("count", 1)), agents.MAX_AT_ONCE))
        total = (len(requested_models) if requested_models else 1) * count
        if total > agents.MAX_PER_START:
            return fail(ValueError(
                f"that's {total} helpers at once; one start can begin at most {agents.MAX_PER_START}: "
                "pick fewer models or fewer of each"))
        if requested_models and options.get("model"):
            return fail(ValueError("choose specific models or one model, not both"))
        picked: list[str] = []
        try:
            net.project_row(slug)
            net.warm_briefs_soon(slug)  # ready before the helpers' first claims
            if given.get("effort") in kinds.get(kind).efforts:  # an endpoint helper's (the others' rides on the model)
                options["effort"] = given["effort"]
            if kinds.get(kind).uses_endpoints:
                if requested_models:
                    if any(not isinstance(model, dict) or not model.get("endpoint") for model in requested_models):
                        raise ValueError("choose API models from a saved endpoint")
                    choices = [(str(model["endpoint"]), str(model["model"])) for model in requested_models]
                    entries = {eid: settings.endpoint(eid) for eid, _ in choices}
                    if any(entry is None for entry in entries.values()):
                        raise ValueError("an endpoint you picked isn't saved any more: pick again")
                    per_endpoint = {eid: sum(1 for chosen, _ in choices if chosen == eid) * count for eid in entries}
                    for eid, needed in per_endpoint.items():
                        entry = entries[eid]
                        active = sum(1 for sess in manager.sessions.values() if sess.options.get("endpoint") == eid
                                     and sess.state in agents.LIVE and not sess.stop.is_set())
                        if entry.get("max_sessions") and active + needed > int(entry["max_sessions"]):
                            raise ValueError(f"{entry['name']} allows {entry['max_sessions']} sessions at once; reduce the selected models or stop a session")
                    ids = []
                    for eid, model in choices:
                        entry = entries[eid]
                        ids.extend(manager.start(kind, slug, count, options | session_options(entry) | {
                            "model": model, "picked_model": model}))
                    picked = list(dict.fromkeys(eid for eid, _ in choices))
                else:
                    picked = [str(e) for e in (given.get("endpoints") or ([given["endpoint"]] if given.get("endpoint") else []))]
                    plan = spread_sessions(settings, manager, picked, count, given)
                    ids = []
                    for entry, n in plan:
                        entry_options = options | session_options(entry)
                        if given.get("model"):
                            chosen = str(given["model"])
                            entry_options["model"] = chosen
                            entry_options["picked_model"] = chosen
                        ids.extend(manager.start(kind, slug, n, entry_options))
            elif requested_models:
                if any(not isinstance(model, str) for model in requested_models):
                    raise ValueError("API endpoint/model selections are only valid for API agents")
                found = next((row for row in manager.detected() if row["id"] == kind), None)
                model_ids = {str(model["id"]) for model in (found or {}).get("models", [])
                             if isinstance(model, dict) and model.get("id")}
                missing = [model for model in requested_models if model not in model_ids]
                if missing and model_ids:
                    raise ValueError("some selected models are no longer available; look again for installed agents and their models")
                ids = []
                for model in requested_models:
                    if isinstance(model, dict):
                        raise ValueError("API model selections are only valid for API agents")
                    if options.get("effort"):
                        model = f"{model.split('#')[0]}#{options['effort']}"
                    ids.extend(manager.start(kind, slug, count, options | {"model": model}))
            else:
                ids = manager.start(kind, slug, count, options)
        except (NetworkError, ValueError) as e:
            return fail(e)
        # remembered, so the next start is one click; models typed in come back as suggestions
        model = str(options.get("model") or "")
        settings.data["launcher"] = {"kind": kind, "model": model, "models": requested_models,
                                     "multiple": bool(given.get("multiple_models")), "count": count} | (
                                         {"endpoints": picked} if picked else {})
        if kinds.get(kind).uses_endpoints and model and picked:
            settings.data["launcher"]["api_model"] = {"endpoint": picked[0], "model": model}
        if not requested_models and model and not kinds.get(kind).uses_endpoints:
            recent = settings.data.setdefault("recent_models", {}).setdefault(kind, [])
            settings.data["recent_models"][kind] = [model] + [m for m in recent if m != model][:7]
        settings.save()
        return JSONResponse({"started": ids})

    @mcp.custom_route("/api/agents/stop", methods=["POST"])
    async def api_agents_stop(request: Request) -> Response:
        """Stop a helper, remove it, remove paused helpers on a project, or stop every helper there."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if data.get("remove"):
            await call(manager.remove, str(data.get("id")))
        elif data.get("remove_paused") and data.get("slug"):
            removed = await call(manager.remove_paused, str(data["slug"]))
            return JSONResponse({"ok": True, "removed": removed})
        elif data.get("slug"):
            await call(manager.stop_project, str(data["slug"]))
        elif data.get("id"):
            await call(manager.stop, str(data["id"]))
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/agents/restart", methods=["POST"])
    async def api_agents_restart(request: Request) -> Response:
        """Start a stopped or paused helper again."""
        if (bad := guard(request, write=True)):
            return bad
        try:
            await call(manager.restart, str((await body(request)).get("id", "")))
        except ValueError as e:
            return fail(e)
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
                if sess.options.get("endpoint") == str(data["remove_endpoint"]) and sess.state in agents.LIVE:
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
        if "notes_cleared_to" in data:  # the notification centre's Clear all: events up to this id stay hidden
            settings.data["notes_cleared_to"] = max(0, int(data["notes_cleared_to"] or 0))
        if "bridges_removed_seen" in data:  # the notice of what the cleanup removed (cleanup.report), seen
            settings.data["bridges_removed_seen"] = bool(data["bridges_removed_seen"])
        settings.save()
        return JSONResponse(settings.public())
