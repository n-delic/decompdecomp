"""Compilers and tools (Setup), agents: installing them, finding which are installed, starting, stopping and
restarting helpers, and the settings.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from decompnet.hostenv import NO_WINDOW

from ... import agents as kinds
from ... import hostenv, linuxvm, toolchains
from ...network import NetworkError
from .. import agents, maintenance, workers
from ..support import session_options, spread_sessions
from . import App

MAX_ENDPOINT_MODELS = 100  # an endpoint may list hundreds; the launcher shows this many at most


def register(app: App) -> None:
    net, mcp, manager, jobs = app.net, app.mcp, app.manager, app.jobs
    settings, guard, body, call, fail = app.settings, app.guard, app.body, app.call, app.fail

    @mcp.custom_route("/api/sdk-library", methods=["GET", "POST"])
    async def api_sdk_library(request: Request) -> Response:
        sdk_library, = await app.imports('decompnet.app', ('sdk_library',))
        if (bad := guard(request, write=request.method == "POST")):
            return bad
        try:
            if request.method == "GET":
                return JSONResponse(await call(lambda: sdk_library.state(net, request.query_params.get('slug'))))
            data = await body(request)
            if data.get('slug'):
                await call(lambda: sdk_library.choose(net, str(data['slug']), data.get('selection')))
                purpose_work, sdk_names = await app.imports('decompnet.app', ('purpose_work', 'sdk_names'))
                await app.control(lambda: purpose_work.start('SDK function naming', str(data['slug']), sdk_names.name_functions, net, str(data['slug'])))
                return JSONResponse(await call(lambda: sdk_library.state(net, str(data['slug']))))
            folders = data.get('folders', [])
            return JSONResponse({'job': await app.control(lambda: jobs.start('sdk-library', lambda log: sdk_library.scan(net, folders, log)))})
        except (NetworkError, ValueError, OSError) as e:
            return fail(e)

    @mcp.custom_route("/api/linux-vm", methods=["POST"])
    async def api_linux_vm(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        return JSONResponse({'job': await app.control(lambda: jobs.start('linux-vm', lambda log: linuxvm.setup(log=log)))})

    @mcp.custom_route("/api/toolchain", methods=["POST"])
    async def api_toolchain(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        tid, accept = str(data.get("id", "")), bool(data.get("accept_license"))
        folder = str(data.get("folder") or "").strip() or None
        if folder:
            folder = await app.call(hostenv.local_path, folder)
        return JSONResponse({"job": await app.control(lambda: jobs.start("toolchain", lambda log: (toolchains.install(tid, accept_license=accept, force=bool(data.get("force")), folder=folder, log=log)
                                                                         and {"id": tid})))})

    @mcp.custom_route("/api/toolchain/remove", methods=["POST"])
    async def api_toolchain_remove(request: Request) -> Response:
        """Forget one of the user's own copies of a kit (its files stay where they are)."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            await app.control(lambda: toolchains.remove_local(str(data.get("id", "")), str(data.get("folder", ""))))
        except (toolchains.ToolchainError, KeyError) as e:
            return fail(e)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/toolchain/all", methods=["POST"])
    async def api_toolchain_all(request: Request) -> Response:
        """Every compiler of a project's console, then its compiler test again (Engine.install_compilers)."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug, accept = str(data.get("slug", "")), bool(data.get("accept_license"))
        return JSONResponse({"job": await app.control(lambda: jobs.start("toolchain", lambda log: net.install_compilers(slug, accept, log=log)))})

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
        if kind.install_text().startswith("npm ") and not await app.call(shutil.which, "npm"):
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
        return JSONResponse({"job": await app.control(lambda: jobs.start("agent-install", work))})

    @mcp.custom_route("/api/agents/detect", methods=["POST"])
    async def api_agents_detect(request: Request) -> Response:
        """Look again which agents are installed and their models (a sign-in since): in the background."""
        if (bad := guard(request, write=True)):
            return bad
        AGENTS, = await app.imports('decompnet.agents', ('AGENTS',))
        for agent in AGENTS:
            await app.control(agent.forget_models)
        await app.control(manager.refresh_detected)
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
        if data.get('background'):
            def create(log):
                token = agents.start_progress.set(log)
                try:
                    log('Checking project, model and work-selection settings…')
                    response = create_agents(data)
                    result = json.loads(response.body)
                    if response.status_code >= 400:
                        raise ValueError(result.get('error') or 'Agent creation failed.')
                    log(f"Created {len(result['started'])} agents. Watch their cards for preparation, sign-in, limits or available-work status.")
                    return result
                except Exception:
                    log('Creation stopped. Check the Agents list before retrying: earlier steps may already have created a worker.')
                    raise
                finally:
                    agents.start_progress.reset(token)
            try:
                def reserve():
                    if (data.get('options') or {}).get('task')!='assets':return jobs.start('Create agents',create,project=str(data.get('slug','')))
                    with jobs._lock:
                        old=next((job for job in jobs.jobs.values() if job['kind']=='Create asset discovery agents' and job['state']=='running' and job.get('activity_project')==data.get('slug')),None)
                        return old['id'] if old else jobs.start('Create asset discovery agents',create,project=str(data.get('slug','')),cancel_message='Asset discovery agent creation was stopped.')
                return JSONResponse({'job':await app.control(reserve)})
            except ValueError as error:
                return fail(error)
        # Legacy callers keep the immediate response, but preparation must never block the event loop.
        return await app.control(create_agents, data)

    def create_agents(data):
        kind, slug = str(data.get("kind", "")), str(data.get("slug", ""))
        given = data.get("options") or {}
        if data.get('replace'):
            try:
                if data.get('count', 1) != 1 or given.get('models') or given.get('multiple_models'):
                    raise ValueError('Changing a model replaces exactly one agent.')
                ids = manager.change_model(str(data['replace']), slug, kind, given.get('model'))
                return JSONResponse({'started': ids})
            except (NetworkError, ValueError) as error:
                return fail(error)
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
        options = {k: v for k, v in given.items() if k in ("model", "per_session", "task", "selection", "focus_function", "focus_minutes", "boundary_function", "asset_scope", "asset_run")}  # task: "readable" polishes
        options.setdefault("per_session", settings.data.get("per_session", agents.PER_SESSION))
        count = max(1, min(int(data.get("count", 1)), agents.MAX_AT_ONCE))
        total = (len(requested_models) if requested_models else 1) * count
        if total > agents.MAX_PER_START:
            return fail(ValueError(
                f"that's {total} helpers at once; one start can begin at most {agents.MAX_PER_START}: "
                "pick fewer models or fewer of each"))
        if requested_models and options.get("model"):
            return fail(ValueError("choose specific models or one model, not both"))
        if options.get('task') == 'boundary' and total != 1:
            return fail(ValueError('Choose one agent and one model for a boundary investigation.'))
        if options.get('focus_function') and total!=1:
            return fail(ValueError('Choose one agent and one model for a focused run.'))
        picked: list[str] = []
        try:
            if given.get("account_id"):
                options.update(settings.account_options(kind, str(given["account_id"])))
            agents.report_start("Reading project metadata…")
            if options.get('focus_function') or options.get('task')=='assets':
                with net.progress.view():
                    net.project_row(slug)
            else:
                net.project_row(slug)
            if not options.get('focus_function') and options.get("task") not in {"readable", "boundary", "assets"}:
                net.warm_briefs_soon(slug)  # matching briefs and machine drafts are not polishing inputs
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
                                     "multiple": bool(given.get("multiple_models")), "count": count,
                                     "account_id": options.get("account_id") or ""} | (
                                         {"endpoints": picked} if picked else {})
        if kinds.get(kind).uses_endpoints and model and picked:
            settings.data["launcher"]["api_model"] = {"endpoint": picked[0], "model": model}
        if not requested_models and model and not kinds.get(kind).uses_endpoints:
            recent = settings.data.setdefault("recent_models", {}).setdefault(kind, [])
            settings.data["recent_models"][kind] = [model] + [m for m in recent if m != model][:7]
        settings.save()
        return JSONResponse({"started": ids})

    @mcp.custom_route("/api/agents/reset-metrics", methods=["POST"])
    async def api_agents_reset_metrics(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            reset = await call(lambda: manager.reset_metrics(str(data.get('id', ''))))
        except ValueError as e:
            return fail(e)
        return JSONResponse({'reset': True, **(reset or {})})

    @mcp.custom_route("/api/agents/rename", methods=["POST"])
    async def api_agents_rename(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            label = await call(manager.rename, str(data.get("id", "")), data.get("label", ""))
        except ValueError as e:
            return fail(e)
        return JSONResponse({"label": label})

    @mcp.custom_route("/api/agents/retry-provider", methods=["POST"])
    async def api_agents_retry_provider(request: Request) -> Response:
        if (bad := guard(request, write=True)):return bad
        data=await body(request)
        try:return JSONResponse(await call(manager.retry_provider,str(data.get('id',''))))
        except (NetworkError,ValueError) as error:return fail(error)

    @mcp.custom_route("/api/agents/retry-paused", methods=["POST"])
    async def api_agents_retry_paused(request: Request) -> Response:
        if (bad := guard(request, write=True)):return bad
        data=await body(request)
        try:
            if data.get('background'):
                return JSONResponse({'job':await app.control(lambda: jobs.start('Retry paused functions',
                    lambda log: manager.retry_paused(str(data.get('id','')),data.get('limit',5),log=log)))})
            result=await call(manager.retry_paused,str(data.get('id','')),data.get('limit',5))
        except (NetworkError,ValueError) as error:return fail(error)
        return JSONResponse(result)

    @mcp.custom_route("/api/agents/selection", methods=["POST"])
    async def api_agents_selection(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            policy = await call(manager.set_selection, str(data.get("id", "")), data.get("selection") or {}, data.get("per_session"))
        except (NetworkError, ValueError) as e:
            return fail(e)
        return JSONResponse({"selection": policy})

    @mcp.custom_route("/api/agents/stop", methods=["POST"])
    async def api_agents_stop(request: Request) -> Response:
        """Stop a helper, remove it, remove paused helpers on a project, or stop every helper there."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if data.get("remove"):
            await app.control(manager.remove, str(data.get("id")))
        elif data.get("remove_paused") and data.get("slug"):
            removed = await app.control(manager.remove_paused, str(data["slug"]))
            return JSONResponse({"ok": True, "removed": removed})
        elif data.get("slug"):
            await app.control(manager.stop_project, str(data["slug"]))
        elif data.get("id"):
            await app.control(manager.stop, str(data["id"]))
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/agents/login", methods=["POST"])
    async def api_agents_login(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        try:
            sid = str((await body(request)).get("id", ""))
            session = manager.sessions.get(sid)
            if session is None:
                raise ValueError("This helper no longer exists.")
            kind = kinds.get(session.kind)
            exe = await app.call(lambda: agents._which(kind.command))
            if not exe:
                raise ValueError(f"{kind.name} is not installed.")
            await call(kind.login, exe)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            return fail(ValueError(f"Could not open sign-in: {error}"))
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

    @mcp.custom_route("/api/agent-accounts", methods=["POST"])
    async def api_agent_accounts(request: Request) -> Response:
        """Save or forget a sign-in reference; authentication remains in the agent's own CLI."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            if data.get("remove"):
                aid = str(data["remove"])
                settings.remove_account(aid)
                for sid, sess in list(manager.sessions.items()):
                    if sess.options.get("account_id") == aid and sess.state in agents.LIVE:
                        await call(manager.stop, sid)
            else:
                account = await call(settings.save_account, data)
                await call(manager.rename_account, account["id"], account["name"])
            await call(settings.save)
        except (ValueError, OSError) as e:
            return fail(ValueError(str(e)) if isinstance(e, ValueError) else
                        ValueError("The account could not be saved. Check that its directory is accessible."))
        return JSONResponse(settings.public())

    @mcp.custom_route("/api/settings", methods=["POST"])
    async def api_settings(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        def save_settings():
            if "maintenance_cpu_percent" in data and (type(data["maintenance_cpu_percent"]) is not int
                    or not 0 <= data["maintenance_cpu_percent"] <= 100):
                return fail(ValueError("Enter a whole CPU percentage from 1 to 100, or 0 for unlimited."))
            if any(key in data for key in ('agent_local_enabled','agent_local_trials','agent_local_seconds')):
                from ..agent_local_pass import configure
                if 'agent_local_enabled' in data and not isinstance(data['agent_local_enabled'],bool):
                    return fail(ValueError('Automatic local repairs must be true or false.'))
                for key,maximum in (('agent_local_trials',6),('agent_local_seconds',15)):
                    if key in data and (isinstance(data[key],bool) or not isinstance(data[key],int) or not 1<=data[key]<=maximum):
                        return fail(ValueError(f'{key}: choose an integer from 1 to {maximum}.'))
                settings.data.update({key:data[key] for key in ('agent_local_enabled','agent_local_trials','agent_local_seconds') if key in data})
                configure(net,settings.data)
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
            if 'iterative_enabled' in data:
                settings.data['iterative_enabled'] = bool(data['iterative_enabled'])
            if 'reuse_auto' in data:
                settings.data['reuse_auto'] = bool(data['reuse_auto'])
            for key, maximum in [('reuse_seconds', 600), ('reuse_attempts', 1024), ('iterative_seconds', 600), ('iterative_attempts', 1024)]:
                if key in data:
                    settings.data[key] = max(1, min(maximum, int(data[key])))
            if 'iterative_threads' in data:  # nothing or 0: as many as the computer can spare
                settings.data['iterative_threads'] = max(0, int(data['iterative_threads'] or 0))
            if any(key in data for key in ('reuse_auto', 'reuse_seconds', 'reuse_attempts', 'iterative_enabled', 'iterative_seconds', 'iterative_attempts', 'iterative_threads')):
                net.cascades.configure(settings.data.get('reuse_auto', True), settings.data.get('reuse_seconds', 120),
                                       settings.data.get('reuse_attempts', 128), settings.data.get('iterative_enabled', False),
                                       settings.data.get('iterative_seconds', 120), settings.data.get('iterative_attempts', 128),
                                       settings.data.get('iterative_threads', 0))
            for key, maximum in [('permuter_max_insns', 100000), ('permuter_seconds', 3600), ('permuter_max_turns', 1000)]:
                if key in data:
                    settings.data[key] = max(1, min(maximum, int(data[key])))
            if any(key in data for key in ('permuter_max_insns', 'permuter_seconds', 'permuter_max_turns')):
                net.permuter.configure(settings.data)
            if "maintenance_cpu_percent" in data:
                maintenance.configure_cpu(data["maintenance_cpu_percent"])
                settings.data["maintenance_cpu_percent"] = data["maintenance_cpu_percent"]
            if "worker_threads" in data:  # nothing or 0: as many as the computer can spare (workers.py)
                settings.data["worker_threads"] = max(0, int(data["worker_threads"] or 0))
                workers.configure(net.permuter.parent_threads(settings.data["worker_threads"]))
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
            if "rom_library" in data:
                settings.data["rom_library"] = str(data["rom_library"] or "").strip()
            settings.save()
            return JSONResponse(settings.public())
        return await app.call(save_settings)
