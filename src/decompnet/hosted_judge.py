"""The judge a verifier runs (gateway/src/verifier.rs): compile a hosted submission like decompdecomp does.

    decomp judge-worker      # one JSON request per line on stdin, one reply per line on stdout

A hosted server's agents compile on their own machines; a match counts once a trusted machine has compiled the same
C into the same bytes. This judges with this computer's own copy of each game, its compilers and linker: the exact
judge the app uses (RomNetwork._judge_with), so a verified match here is a match in the app.

Requests:
- {"op": "describe"} -> {"projects": [slug, ...], "toolchains": [id, ...], "version": ...}: the ROM projects in
  this home and the compilers installed.
- {"op": "judge", "slug", "function": {"name", "vram", "target" (hex)}, "compiler": {"toolchain", "flags"},
  "source"} -> {"score": 0 for a match, the difference otherwise, null when it didn't compile; "detail"}, or
  {"error": ...} when it can't be judged here. That happens when the project isn't here, or its function's target
  bytes differ from the hosted ones (another build of the game).

Opened as the reader process does (store.ATTACH_ONLY, no saved-progress sync), so it can run beside the app.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def describe(net) -> dict:
    from . import __version__, toolchains
    from .network import Network
    projects = []
    for row in net.store.all("SELECT slug FROM projects ORDER BY slug"):
        try:
            proj = Network._project(net, row["slug"])
        except Exception:
            continue
        if net.cfg(proj).get("kind") != "fork":
            projects.append(row["slug"])
    installed = sorted(tid for tid in toolchains.registry() if _installed(toolchains, tid))
    return {"projects": projects, "toolchains": installed, "version": __version__}


def _installed(toolchains, tid) -> bool:
    try:
        return toolchains.installed(tid) is not None
    except Exception:
        return False


def judge(net, request: dict) -> dict:
    from .network import Network
    slug = str(request.get("slug") or "")
    wanted = request.get("function") or {}
    compiler = request.get("compiler") or {}
    source = request.get("source")
    if not isinstance(source, str) or not source.strip():
        return {"score": None, "detail": "no source"}
    try:
        proj = Network._project(net, slug)
    except Exception:
        return {"error": f"this computer has no project {slug!r}"}
    if net.cfg(proj).get("kind") == "fork":
        return {"error": f"{slug} is a fork project; only ROM projects are judged here"}
    store = net.project_store(proj["id"])
    rows = store.all("SELECT * FROM functions WHERE project_id=? AND name=? AND vram IS ? AND gone_mark IS NULL",
                     proj["id"], wanted.get("name"), wanted.get("vram"))
    if len(rows) != 1:
        return {"error": f"{slug} here has {len(rows)} functions named {wanted.get('name')!r} at that address"}
    fn = rows[0]
    try:
        expected = bytes.fromhex(str(wanted.get("target") or ""))
    except ValueError:
        return {"error": "the target bytes aren't hex"}
    if bytes(fn["target"] or b"") != expected:
        return {"error": f"{slug}'s {fn['name']} here has other target bytes: another build of the game"}
    toolchain = compiler.get("toolchain")
    flags = compiler.get("flags") or []
    if not toolchain:
        return {"error": "no compiler named"}
    matched, diff, log = net._judge_with(proj, fn, toolchain, list(flags), source)
    if matched:
        return {"score": 0, "detail": "byte-identical"}
    if diff is None:
        return {"score": None, "detail": (log or "it didn't compile")[-2000:]}
    return {"score": max(1, int(diff.score)), "detail": (log or "")[-2000:]}


def serve(net, lines=sys.stdin, out=sys.stdout) -> None:
    for line in lines:
        try:
            request = json.loads(line)
            if request.get("op") == "describe":
                reply = describe(net)
            elif request.get("op") == "judge":
                reply = judge(net, request)
            else:
                reply = {"error": f"unknown op {request.get('op')!r}"}
        except Exception as error:  # this request only: the verifier releases it for another
            reply = {"error": f"{type(error).__name__}: {error}"}
        out.write(json.dumps(reply) + "\n")
        out.flush()


def main(home: Path) -> int:
    from . import store as storage
    storage.ATTACH_ONLY = True
    from .app.engine import RomNetwork
    from .app.tool_reader import reader_progress
    net = RomNetwork(Path(home) / ".decomp" / "decomp.db", Path(home), project_databases=True)
    reader_progress(net)  # never synchronizes saved progress folders: the app keeps them
    # Replies go on the real stdout; anything else printed (compiler chatter) goes to stderr.
    out, sys.stdout = sys.stdout, sys.stderr
    serve(net, sys.stdin, out)
    return 0
