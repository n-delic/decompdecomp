"""Origin-bound browser tasks; existing private app routes keep their original window-only guard."""
from __future__ import annotations

import json

from starlette.responses import JSONResponse, Response

from ...network import NetworkError
from ..browser_bridge import Bridge, validate_source

MAX_BODY = 320 * 1024


def register(app):
    bridge = Bridge(app.net)
    app.net.browser_bridge = bridge

    async def data(request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY:
                raise ValueError('Browser request exceeds 320 KiB.')
        value = json.loads(raw or b'{}')
        if not isinstance(value, dict):
            raise ValueError('Send a JSON object.')
        return value

    @app.mcp.custom_route('/api/browser-bridge/authorize', methods=['POST'])
    async def authorize(request):
        if bad := app.guard(request, write=True):
            return bad
        try:
            body = await data(request)
            slug = str(body.get('slug') or '')
            if body.get('action') == 'revoke':
                return JSONResponse(bridge.revoke(slug))
            return JSONResponse(await app.call(bridge.create_code, slug, str(body.get('origin') or '')))
        except (ValueError, NetworkError, OSError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/browser-bridge/{action}', methods=['POST', 'OPTIONS'])
    async def browser(request):
        origin = request.headers.get('origin', '')
        if request.headers.get('host') not in app.allowed_hosts or not bridge.allowed(origin):
            return JSONResponse({'error':'This browser origin has not been paired in the desktop app.'}, status_code=403)
        headers = {'Access-Control-Allow-Origin':origin, 'Vary':'Origin', 'Cache-Control':'no-store',
                   'Access-Control-Allow-Methods':'POST, OPTIONS', 'Access-Control-Allow-Headers':'Authorization, Content-Type',
                   'Access-Control-Allow-Private-Network':'true', 'Access-Control-Max-Age':'60'}
        if request.method == 'OPTIONS':
            return Response(status_code=204, headers=headers)
        try:
            body = await data(request)
            action = request.path_params['action']
            if action == 'pair':
                return JSONResponse(bridge.pair(origin, body.get('code', '')), headers=headers)
            auth = request.headers.get('authorization', '')
            if not auth.startswith('Bearer '):
                raise PermissionError('Pair this browser first.')
            key, session = bridge.auth(origin, auth[7:])
            if action == 'functions':
                result = await app.call(bridge.listing, session, body.get('search', ''))
            elif action == 'task':
                fid = body.get('functionId')
                if isinstance(fid, bool) or not isinstance(fid, int) or not 0 < fid < 2**63:
                    raise ValueError('Choose a numeric function ID from the paired project.')
                result = await app.call(bridge.task, key, session, fid)
            elif action == 'submit':
                source = body.get('source')
                if not isinstance(source, str) or not source.strip() or len(source.encode()) > 128*1024:
                    raise ValueError('Submit nonempty C source up to 128 KiB.')
                validate_source(source)
                variant_id=body.get('variantId')
                if variant_id is not None and (not isinstance(variant_id, str) or len(variant_id)>64):
                    raise ValueError('Choose a host compiler variation.')
                task = bridge.consume(key, session, str(body.get('taskId') or ''))
                try:
                    jid = app.jobs.start('browser-check', lambda log: bridge.submit(key, session, task, source, variant_id))
                except Exception:
                    session['busy'] = False
                    raise
                # This opaque job is also capability scoped: the general /api/job route remains private.
                session['job'] = jid
                result = {'job':jid}
            elif action == 'result':
                jid = body.get('job')
                if not jid or jid != session.get('job'):
                    raise PermissionError('This job does not belong to this browser pairing.')
                job = app.jobs.jobs.get(jid)
                if job is None:
                    raise ValueError('The host job is no longer available.')
                result = {k:job.get(k) for k in ('state', 'result', 'error')}
            else:
                raise ValueError('Unknown browser bridge action.')
            return JSONResponse(result, headers=headers)
        except PermissionError as error:
            return JSONResponse({'error':str(error)}, status_code=401, headers=headers)
        except (ValueError, NetworkError, OSError) as error:
            return JSONResponse({'error':str(error)}, status_code=400, headers=headers)
