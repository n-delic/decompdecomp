"""A client that resets a queued connection must not take down the local server."""

import asyncio
import socket
import struct
import sys

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from decompnet.app import cli


def test_server_accepts_connections_after_a_client_reset(monkeypatch):
    async def live(request):
        return PlainTextResponse("live")

    app = Starlette(routes=[Route("/live", live)])
    with cli._listen(0) as listener:
        address = listener.getsockname()

        class ResetTestServer(uvicorn.Server):
            async def main_loop(self):
                # Let the event loop register its accept, then keep it busy while the first
                # connection occupies that accept and the second resets in the listen queue.
                await asyncio.sleep(0)
                with socket.create_connection(address, timeout=2):
                    with socket.create_connection(address, timeout=2) as reset:
                        linger = struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0)
                        reset.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
                    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                        response = await client.get(f"http://127.0.0.1:{address[1]}/live")
                    assert response.status_code == 200
                    assert response.text == "live"
                if sys.platform == "win32":
                    assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)

        monkeypatch.setattr(uvicorn, "Server", ResetTestServer)
        cli.serve(app, listener)
