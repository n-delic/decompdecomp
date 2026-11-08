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

    share = {"servers": {}, "tunnel": None, "links": {}, "restore_errors": {}, "restore_job": None}
    room_lock = threading.RLock()

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
        # The Windows app runs under pythonw, where stdout is None; Uvicorn's default formatter calls stdout.isatty().
        # The app's file logger already records room-server errors, so this server needs no terminal logging.
        server = uvicorn.Server(uvicorn.Config(collab.host_app(net, net.rooms), host=host, port=port, log_level="warning",
                                               log_config=None, access_log=False))
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

    def _open_room(slug: str, mode: str, address: str = "", port: int = 0, log=print, keep: bool = False) -> str:
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

    def open_room(*args, **kwargs):
        with room_lock:
            link = _open_room(*args, **kwargs)
            share['restore_errors'].pop(args[0], None)
            return link

    if any((json.loads(p['config']).get('collab') or {}).get('open')
           for p in (net.project_row(row['slug']) for row in net.store.all('SELECT slug FROM projects'))):
        from ..hosted_rooms import restore
        def reopen(log):
            # Serialize startup with explicit stop/rotate so a closed room cannot reopen behind the user.
            with room_lock:
                share['restore_errors'] = restore(net, open_room, log)
            return {'errors':share['restore_errors']}
        share['restore_job'] = jobs.start('Restore hosted rooms', reopen)

    @mcp.custom_route("/api/collab", methods=["GET"])
    async def api_collab(request: Request) -> Response:
        if (bad := guard(request)):
            return bad
        slug = request.query_params.get("slug", "")
        def snapshot():
            # Project/room helpers may synchronize progress. This UI read must not wait
            # for thousands of rechecks, nor run database/network work on the event loop.
            with net.progress.view():
                info = net._room_info(slug)
                room = net.rooms.get(slug)
                cfg = (json.loads(net.project_row(slug)["config"]).get("collab") or {}) if slug else {}
                return JSONResponse({"sharing": room is not None, "link": share["links"].get(slug), "room": info,
                                     "settings": room.settings if room else {"visibility": cfg.get("visibility", "private"),
                                                                               "approve": bool(cfg.get("approve"))},
                                     "pending": room.pending() if room else [],
                                     "mode": cfg.get("mode", "tunnel"), "address": cfg.get("address", ""), "port": cfg.get("port", 6481),
                                     "was_sharing": bool(cfg.get("open")) and room is None,
                                     "restoring": bool(share['restore_job'] and jobs.jobs.get(share['restore_job'], {}).get('state') == 'running'),
                                     "restore_error": share['restore_errors'].get(slug),
                                     "stable_link": cfg.get('mode') == 'address',
                                     "addresses": collab.local_addresses(),
                                     "tunnel_supported": collab.cloudflared_available()})
        try:
            return await (app.ui_call or call)(snapshot)
        except (NetworkError, OSError) as error:
            return fail(error)

    @mcp.custom_route('/api/collab/matches', methods=['GET','POST'])
    async def api_room_matches(request: Request) -> Response:
        if (bad := guard(request,write=request.method=='POST')):
            return bad
        room_matches, = await app.imports('decompnet.app', ('room_matches',))
        data=await body(request) if request.method=='POST' else dict(request.query_params)
        slug=str(data.get('slug') or '')
        try:
            await app.read(net.project_row, slug)
            room_reconcile, = await app.imports('decompnet.app', ('room_reconcile',))
            room=net.rooms.get(slug)
            def reconciliation_job():
                with jobs._lock:current=list(jobs.jobs.values())
                return next((j['id'] for j in current if j.get('scope')=={'slug':slug,'task':'room-reconcile'} and j['state']=='running'),None)
            def start_reconciliation():
                with room_reconcile.lock(room):
                    identity=reconciliation_job()
                    if not identity:
                        identity=jobs.start('room match reconciliation',lambda log:room_reconcile.run(room,log),
                                            cancel_message='Reconciliation stopped. Verified matches are kept; run again to resume missing matches.')
                        jobs.jobs[identity]['scope']={'slug':slug,'task':'room-reconcile'}
                    return {'job':identity}
            if request.method=='GET':
                state=await app.read(room_reconcile.snapshot,room) if room else None
                if (state and state['active'] and any(r['status'] in ('queued','verifying') for r in state['results'].values())
                        and not reconciliation_job()):
                    await app.read(start_reconciliation)
                return JSONResponse({'rows':await app.read(room_matches.public,net,slug),'guest':slug in net.links,
                                     'reconciliation':state,'reconcile_job':reconciliation_job()})
            if data.get('reconcile'):
                if not room or slug in net.links:
                    raise NetworkError('Start sharing this project from the host before reconciling room matches.')
                return JSONResponse(await app.read(start_reconciliation))
            if await app.read(net.fork_slug, slug):
                raise NetworkError('Imported-project rooms verify their source tree/build settings; portable compiler/header bundles currently support ROM projects.')
            key=str(data.get('key') or '')
            if key and not key.startswith(room_matches.PREFIX+str((await app.read(net.project_row, slug))['id'])+':'):
                raise NetworkError('Choose a submission in this project.')
            def verify_pending(log):
                log.progress({'finished':0,'total':0,'phase':'Loading pending room submissions'})
                if data.get('recover') and slug not in net.links:
                    log.progress({'finished':0,'total':0,'phase':'Recovering older submissions'})
                    log(f'Recovered {room_matches.recover_legacy(net,slug)} older submissions.')
                rows=[r for r in room_matches.pending(net,slug) if not key or r['key']==key]
                if data.get('all') is not True:
                    rows=rows[:5]
                matched=failed=0
                results=[]
                for index,row in enumerate(rows):
                    if getattr(log,'cancelled',False):break
                    if hasattr(log,'progress'):log.progress({'finished':index,'total':len(rows),'phase':'Host verification','function':row['function']})
                    log(f"Verifying {row['function']} ({index+1}/{len(rows)})")
                    try:
                        if row['direction']=='client':
                            if slug not in net.links:raise NetworkError('Reconnect to this room before resubmitting.')
                            log.progress({'finished':index,'total':len(rows),'function':row['function'],
                                          'phase':'Sending saved match to the host and waiting for verification'})
                            result=room_matches.resend(net.links[slug],row['key'])
                        else:
                            def report(value, index=index, row=row):
                                log.progress({'finished':index,'total':len(rows),'function':row['function'],'results':list(results),**value})
                            result=room_matches.retry(net,slug,row['key'],progress=report)
                        results.append({'key':row['key'],'function':row['function'],'matched':bool(result.get('matched')),'rejected':bool(result.get('rejected')),
                                        'reason':result.get('reason') or ('Host verified exact bytes' if result.get('matched') else 'Still pending'),
                                        'compiler':result.get('compiler')})
                        matched+=bool(result.get('matched'))
                        log(result.get('reason') or ('Host confirmed match' if result.get('matched') else 'Still pending'))
                    except (NetworkError,ValueError) as error:
                        failed+=1
                        results.append({'key':row['key'],'function':row['function'],'matched':False,'reason':str(error)})
                        if data.get('all') is not True:
                            log.progress({'finished':index+1,'total':len(rows),'phase':'Verification needs attention','results':list(results)})
                            raise
                        log(f"Could not verify {row['function']}: {error}")
                    if hasattr(log,'progress'):log.progress({'finished':index+1,'total':len(rows),'phase':'Host verification','results':list(results)})
                return {'matched':matched,'failed':failed,'total':len(rows),'results':results}
            def work(log):
                with net.progress.batch(slug, logger=log):
                    result = verify_pending(log)
                    log.progress({'finished':len(result['results']),'total':result['total'],
                                  'phase':'Saving verified room matches', 'results':result['results']})
                return result
            return JSONResponse({'job':await app.control(lambda: jobs.start('room match verification',work))})
        except (NetworkError,ValueError) as error:return fail(error)

    @mcp.custom_route("/api/collab/start", methods=["POST"])
    async def api_collab_start(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug = str(data.get("slug", ""))
        if slug in net.links:
            return fail(ValueError("this project belongs to a friend's room; only its host can invite people"))
        if await app.read(net.fork_slug, slug) and not json.loads((await app.read(net.project_row, slug))["config"]).get("ready"):
            return fail(ValueError("set the project up first (its Overview): then friends can join it"))
        mode = "address" if data.get("mode") == "address" else "tunnel"

        def work(log):
            room = net.rooms.get(slug) or collab.Room(net, slug)
            room.configure(data.get("visibility"), data.get("approve"))
            return {"link": open_room(slug, mode, str(data.get("address", "")), int(data.get("port") or 6481), log=log,
                                      keep=bool(data.get("keep")))}
        try:
            return JSONResponse({"job": await app.control(lambda: jobs.start("share", work))})
        except (NetworkError, ValueError) as error:
            return fail(error)

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
        def manage():
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

        return await app.call(manage)

    @mcp.custom_route("/api/collab/stop", methods=["POST"])
    async def api_collab_stop(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        def stop():
            with room_lock:
                room = net.rooms.pop(slug, None) or collab.Room(net, slug)
                room.close()  # also closes a saved room whose startup restoration failed
                share['restore_errors'].pop(slug, None)
                share["links"].pop(slug, None)
                if not net.rooms and share["tunnel"] is not None:
                    share["tunnel"].stop()
                    share["tunnel"] = None
            return JSONResponse({"ok": True})

        return await app.call(stop)

    @mcp.custom_route("/api/collab/join", methods=["POST"])
    async def api_collab_join(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        q = request.query_params
        if q.get("confirmed") != "1":
            return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
        link, name = q.get("link", ""), q.get("name", "")
        game: Path | None = None
        uploaded = False
        project = q.get("project", "")
        if not project and q.get("path"):  # a file on this computer (a disc is gigabytes: not through the browser)
            game = await app.call(hostenv.local_path, q["path"])
            if not await app.call(lambda: game.is_file()):
                return fail(ValueError(f"no file at {game}"))
        elif not project:  # the browser's upload, streamed to disk
            upload = home / "uploads" / uuid.uuid4().hex[:8]
            await app.call(lambda: upload.mkdir(parents=True, exist_ok=True))
            game = upload / (Path(q.get("filename") or "game.bin").name or "game.bin")
            size = 0
            async with await anyio.open_file(game, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_DISC:
                        await f.aclose()
                        await app.call(lambda: shutil.rmtree(upload, ignore_errors=True))
                        return fail(ValueError("that file is larger than any game disc"))
                    await f.write(chunk)
            if size == 0:
                await app.call(lambda: shutil.rmtree(upload, ignore_errors=True))
                game = None
            uploaded = game is not None

        def work(log):
            try:
                copy = collab.project_game(net, project) if project else game
                if project:
                    log("Using the game copy already stored in your project; checking it against the host's game.")
                slug = collab.join_room(net, link, name, copy or b"", log=log)
            finally:
                if uploaded:
                    shutil.rmtree(game.parent, ignore_errors=True)
            settings.data["last_project"] = slug
            settings.save()
            return {"slug": slug}
        return JSONResponse({"job": await app.control(lambda: jobs.start("join", work))})

    @mcp.custom_route("/api/collab/reconnect", methods=["POST"])
    async def api_collab_reconnect(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug, link = str(data.get("slug", "")), str(data.get("link", ""))
        return JSONResponse({"job": await app.control(lambda: jobs.start("reconnect", lambda log: {"slug": collab.reconnect(net, slug, link, log=log)}))})

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
        net.shutting_down = True
        for server, _ in list(share["servers"].values()):server.should_exit = True
        if share["tunnel"] is not None:
            share["tunnel"].stop()
        links=list(net.links.values())
        for link in links:link.stop()
        # One authenticated lease release per room, concurrently; an offline host
        # must not cost a timeout for every saved helper on this client.
        def release(link):
            try:link._req('POST','/collab/leave')
            except Exception:jobs.logger.warning('Could not notify room %s of shutdown; remote leases will expire.',link.slug)
        threads=[threading.Thread(target=release,args=(link,),daemon=True,name='room-shutdown-'+link.slug) for link in links]
        for thread in threads:thread.start()
        deadline=time.monotonic()+2
        for thread in threads:thread.join(max(0,deadline-time.monotonic()))

    manager.on_stop = shutdown_rooms
