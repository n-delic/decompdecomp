"""Render saved-progress files in a read-only subprocess."""
from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path


def child(db_path: str, workspace: str) -> None:
    from decompnet import store as storage
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s [render-worker pid=%(process)d] %(message)s")
    storage.ATTACH_ONLY = True
    from decompnet.app import saves
    from decompnet.app.engine import RomNetwork
    from decompnet.app.tool_reader import drop_stale_caches, reader_progress
    from decompnet.app.workers import lower_priority
    lower_priority()  # background work: the app and the agents' compiles come first
    net = RomNetwork(db_path, Path(workspace))
    reader_progress(net)
    seen: dict = {}
    seen_lock = threading.Lock()
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # (anything printed goes to the log, not into a reply)
    out.write(b"READY\n")
    out.flush()
    for line in sys.stdin.buffer:
        try:
            request = json.loads(line)
            slug = request["slug"]
            drop_stale_caches(net, slug, seen, seen_lock)
            readme_key = tuple(request["readme_key"]) if request.get("readme_key") is not None else None
            if request["full"]:
                files, counts = saves._render(net, slug, readme_key=readme_key, write_readme=False)
            else:
                files, counts = saves._render_partial(net, net._project(slug), Path(request["folder"]),
                                                      request["names"], headers=request["headers"],
                                                      readme_key=readme_key, write_readme=False)
            reply = {"id": request.get("id"), "ok": True, "files": files, "counts": counts}
        except Exception as error:
            kind = type(error).__name__
            if isinstance(error, OSError):
                kind = "OSError"
            elif isinstance(error, UnicodeError):
                kind = "UnicodeError"
            elif type(error).__name__ != "JSONDecodeError" and _is_network_error(error):
                kind = "NetworkError"
            reply = {"id": None, "ok": False, "type": kind, "message": str(error)}
        out.write(json.dumps(reply).encode() + b"\n")
        out.flush()


def _is_network_error(error) -> bool:
    from decompnet.network import NetworkError
    return isinstance(error, NetworkError)


if __name__ == "__main__":
    child(sys.argv[1], sys.argv[2])
