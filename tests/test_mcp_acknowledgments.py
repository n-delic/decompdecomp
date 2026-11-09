import json
from types import SimpleNamespace

import anyio
import pytest
from mcp.server.streamable_http import StreamableHTTPServerTransport
from starlette.requests import Request

from decompnet.mcp_acknowledgments import Acknowledgments
from decompnet.ui_diagnostics import Diagnostics


@pytest.mark.parametrize('closed',[True,False])
def test_actual_sdk_notification_queue_returns_one_response_after_delivery(caplog,closed):
    async def run():
        transport=StreamableHTTPServerTransport(None)
        writer,reader=anyio.create_memory_object_stream(1)
        if closed:await writer.aclose()
        transport._read_stream_writer=writer
        body=json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'}).encode()
        scope={'type':'http','method':'POST','path':'/mcp','headers':[(b'accept',b'application/json, text/event-stream'),(b'content-type',b'application/json')], 'query_string':b'private=secret','route':SimpleNamespace(path='/mcp')}
        async def receive():return {'type':'http.request','body':body,'more_body':False}
        messages=[];completed=False
        async def send(message):
            nonlocal completed
            assert not completed,'SDK sent a second response after HTTP completion'
            messages.append(message)
            if message['type']=='http.response.body' and not message.get('more_body'):completed=True
        async def sdk(scope,receive,send):
            await transport._handle_post_request(scope,Request(scope,receive),receive,send)
        await Acknowledgments(Diagnostics(sdk))(scope,receive,send)
        assert [m['status'] for m in messages if m['type']=='http.response.start']==[500 if closed else 202]
        if closed:
            assert 'MCP notification delivery failed' in caplog.text
            assert 'HTTP failure status=500 route=/mcp' in caplog.text
        else:assert reader.receive_nowait().message.method=='notifications/initialized'
        assert 'private=secret' not in caplog.text
        await reader.aclose()
    anyio.run(run)


def test_acknowledgment_waits_for_delivery_and_streamed_tools_are_not_buffered():
    async def run():
        messages=[]
        async def send(message):messages.append(message)
        async def receive():return {'type':'http.disconnect'}
        async def notification(scope,receive,send):
            await send({'type':'http.response.start','status':202,'headers':[]})
            await send({'type':'http.response.body','body':b''})
            assert not messages,'notification was acknowledged before delivery'
        scope={'type':'http','method':'POST','path':'/mcp'}
        await Acknowledgments(notification)(scope,receive,send)
        assert len(messages)==2 and messages[0]['status']==202
        messages.clear()
        async def stream(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'event','more_body':True})
            assert len(messages)==2
            await send({'type':'http.response.body','body':b''})
        await Acknowledgments(stream)(scope,receive,send)
    anyio.run(run)


def test_unexpected_errors_are_not_swallowed_or_acknowledged():
    async def run():
        messages=[]
        async def send(message):messages.append(message)
        async def receive():return {'type':'http.disconnect'}
        async def broken(scope,receive,send):
            await send({'type':'http.response.start','status':202,'headers':[]})
            await send({'type':'http.response.body','body':b''})
            raise ValueError('unexpected')
        with pytest.raises(ValueError,match='unexpected'):
            await Acknowledgments(broken)({'type':'http','method':'POST','path':'/mcp'},receive,send)
        assert not messages
    anyio.run(run)
