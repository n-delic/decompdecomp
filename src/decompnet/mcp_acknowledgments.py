"""A notification is accepted only after the MCP transport has queued it."""
import logging

from anyio import BrokenResourceError, ClosedResourceError
from starlette.responses import JSONResponse


class Acknowledgments:
    """Defer only MCP's empty 202 until delivery completes; never buffer tool streams.

    MCP 2.3 sends its notification acknowledgment before writing to the session
    queue. If that queue closed during cancellation, it then tries a second HTTP
    response. Preserve its error response instead of acknowledging lost work or
    violating ASGI. The transport error remains durably logged.
    """
    def __init__(self, app):self.app=app

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope.get('method')!='POST' or scope.get('path') not in ('/mcp','/mcp/'):
            return await self.app(scope,receive,send)
        pending=None
        accepted=False
        status=None
        complete=False
        async def observed(message):
            nonlocal pending,accepted,status,complete
            if message['type']=='http.response.start':
                if message['status']==202:
                    accepted=True
                    pending=message
                    return
                # A delivery failure supersedes the unsent acknowledgment.
                pending=None
                status=message['status']
            elif pending is not None and message['type']=='http.response.body':
                if not message.get('body') and not message.get('more_body',False):return
                # Only the empty notification acknowledgment is deferred.
                await send(pending)
                pending=None
                status=202
            await send(message)
            if message['type']=='http.response.body' and not message.get('more_body',False):complete=True
        try:
            await self.app(scope,receive,observed)
        except (ClosedResourceError,BrokenResourceError) as error:
            if not accepted:raise
            if pending is not None:
                await JSONResponse({'error':'MCP session closed before accepting the notification; reconnect.'},status_code=500)(scope,receive,send)
            elif not (complete and status is not None and status>=400):
                raise
            logging.getLogger('decompdecomp').warning(
                'MCP notification delivery failed transport=%s status=%d; reconnect required',type(error).__name__,status or 500)
            return
        if pending is not None:
            await send(pending)
            await send({'type':'http.response.body','body':b''})
