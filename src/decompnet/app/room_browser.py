"""A member's browser workspace on the restricted room server, never the private app.

Only saved C, headers and numeric verdicts leave the host. Target bytes/disassembly stay
local, just as for desktop rooms. Compilation uses the ordinary engine and its claims.
"""
from __future__ import annotations

import threading
from pathlib import Path

from starlette.responses import FileResponse, Response
from starlette.routing import Route

from ..network import NetworkError

STATIC = Path(__file__).with_name('static') / 'room'


def routes(net, endpoint):
    """Use the room's existing authenticated endpoint wrapper for every workspace action."""
    # One browser compile per room: an invite must not launch an unbounded set of builds.
    locks = {}
    lock_guard = threading.Lock()

    def functions(room, member, data):
        from .local_claims import expiry_ids
        identities = expiry_ids(net,net.project_store(room.slug))
        actor = net.store.one('SELECT id FROM agents WHERE name=? AND owner=? AND origin=?',
                             member.name+'.browser',member.name,room.origin)
        with net.project_store(room.slug).tx() as db:net._expire_claims(db,**identities)
        held = net.project_store(room.slug).one(
            'SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id WHERE f.project_id=? AND c.agent_id=?',
            room.proj['id'],actor['id']) if actor else None
        return {'functions': [dict(row) for row in net.project_store(room.slug).all(
            'SELECT name, status, best_score FROM functions WHERE project_id=? ORDER BY name', room.proj['id'])],
                'held': held['name'] if held else None}

    def task(room, member, data):
        fn = net._function(str(data.get('function', '')), room.slug)
        source = fn['matched_source'] or fn['best_source'] or fn['seed'] or ''
        header = ''
        if room.fork:
            _, fp, _ = net.forks.get(room.slug)
            source = net.forks._current_source(fp, fn) or source
        else:
            header = net._context(room.proj['id'], fn['unit'])['header']
        return {'function': fn['name'], 'source': source, 'header': header,
                'status': fn['status'], 'score': fn['best_score']}

    def compile_source(room, member, data):
        source = data.get('source')
        if not isinstance(source, str) or not source.strip() or len(source.encode('utf-8')) > 65536:
            raise NetworkError('Enter C or C++ source, up to 64 KiB.')
        name = str(data.get('function', ''))
        fn = net._function(name, room.slug)
        if fn['status'] == 'matched':
            raise NetworkError('This function is already matched. Choose an unfinished function.')
        with lock_guard:
            lock = locks.setdefault(room.slug, threading.Lock())
        if not lock.acquire(blocking=False):
            raise NetworkError('Another browser check is running in this room. Try again when it finishes.')
        try:
            agent = room.agent_for(member, 'browser')
            from .local_claims import expiry_ids
            identities = expiry_ids(net,net.project_store(room.slug))
            with net.project_store(room.slug).tx() as db:
                net._expire_claims(db,**identities)
                other = db.execute('SELECT 1 FROM claims WHERE agent_id=? AND function_id<>?',
                                   (agent.id, fn['id'])).fetchone()
                if other:
                    raise NetworkError('Release your previous function before checking another.')
            # The normal engine refuses other contributors' claims, uses the project's
            # compiler/context, and preserves verified matches and better attempts.
            result = net.attempt(agent, name, source, room.slug)
            return {'score': result['score'], 'matched': result['matched'],
                    'message': ('Verified match. Saved on the host.' if result['matched'] else
                                'The source could not be compiled or compared. Ask the host to check the diagnostics.'
                                if result['score'] is None else
                                f"Mismatch score: {result['score']} (0 means exact). Better attempts are saved automatically.")}
        finally:
            lock.release()

    def release(room, member, data):
        agent = room.agent_for(member, 'browser')
        return {'text': net.release(agent, str(data.get('function', '')), '', room.slug)}

    async def page(request):
        return FileResponse(STATIC / 'index.html', headers={
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; "
                                     "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'X-Content-Type-Options': 'nosniff'})

    async def asset(request):
        # Fixed allowlist, not a path supplied by a guest.
        name = request.path_params['name']
        if name not in ('workspace.js', 'workspace.css'):
            return Response(status_code=404)
        return FileResponse(STATIC / name, headers={'X-Content-Type-Options': 'nosniff'})

    return [Route('/', page), Route('/join/{code}', page), Route('/room/{name}', asset),
            Route('/collab/browser/functions', endpoint(functions)),
            Route('/collab/browser/task', endpoint(task)),
            Route('/collab/browser/compile', endpoint(compile_source), methods=['POST']),
            Route('/collab/browser/release', endpoint(release), methods=['POST'])]
