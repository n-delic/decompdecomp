"""Shared-queue parity with compiler-emitted MIPS ELF, using a homemade verification pack."""

import hashlib
import json
import subprocess
import sys

import pytest

from decompnet import hosted_export, hosted_judge, hosted_pack, toolchains
from decompnet.app import mcp_gateway
from decompnet.app.remote_judge import RemoteJudge
from decompnet.compiler import compile_source
from decompnet.project import compiler_config
from decompnet.rom import match
from tests.test_hosted import gateway
from tests.test_hosted_pack import ROM, game

BASELINE = "volatile int D_80002020 = 7; extern int callee(int); int f1(void) { return callee(D_80002020); }"
SOURCES = [
    BASELINE,
    BASELINE.replace("= 7", "= 8"),
    BASELINE.replace("callee(D_", "callee(1 + D_"),
    BASELINE.replace("callee", "unknown_call"),
    "int f1( /* compile failure */",
]
VRAM = 0x80001100
DATA = 0x80002020
CALLEE = 0x80002200


@pytest.mark.skipif(mcp_gateway.built() is None, reason="the Rust gateway isn't built")
@pytest.mark.parametrize("toolchain", ["gcc-2.7.2-kmc", "ido-7.1"])
def test_real_mips_objects_have_the_same_verdict_on_the_shared_queue(tmp_path, toolchain):
    if not toolchains.installed(toolchain):
        pytest.skip(f"{toolchain} is not installed")
    net = game(tmp_path)
    proj = net.project_row("game", sync=False)
    flags = list(toolchains.get(toolchain).flags)
    compiler = {"toolchain": toolchain, "flags": flags}
    header = "typedef int s32;"
    built = compile_source(compiler_config(tmp_path, {"compiler": compiler}), header, BASELINE)
    assert built.ok, built.log

    # Create a small ROM from the compiler's output at these known symbol addresses.
    with net.store.tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (proj["id"], "callee", CALLEE, 4, "func"))
    target = match.RomTarget("f1", VRAM, b"", [])
    baseline = match.link(built.obj, "f1", target, net.symbols(proj["id"]), lambda *_: None)
    assert baseline.linked_code and not baseline.problems
    symbols = {"callee": CALLEE, "D_80002020": DATA}
    relocs = [(r.offset, r.type, symbols[r.symbol]) for r in baseline.candidate.relocs]
    assert {kind for _, kind, _ in relocs} >= {4, 5, 6}
    rom = bytearray(ROM)
    rom[VRAM - 0x80000000:VRAM - 0x80000000 + len(baseline.linked_code)] = baseline.linked_code
    rom[DATA - 0x80000000:DATA - 0x80000000 + 4] = (7).to_bytes(4, "big")
    (tmp_path / "rom.z64").write_bytes(rom)
    cfg = json.loads(proj["config"])
    cfg["rom"]["sha1"] = hashlib.sha1(rom).hexdigest()
    cfg["compiler"] = compiler
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
        db.execute("UPDATE functions SET target=?, relocs=?, size=?, n_insns=?, rom_offset=? WHERE project_id=? AND name='f1'",
                   (baseline.linked_code, json.dumps(relocs), len(baseline.linked_code),
                    len(baseline.linked_code) // 4, VRAM - 0x80000000, proj["id"]))

    folder = tmp_path / "hosted"
    hosted_export.export(net, "game", folder / "projects" / "game.sqlite3", log=lambda _: None)
    pack = folder / "packs" / "game.sqlite3"
    hosted_pack.export_pack(net, "game", pack)
    digest = hosted_pack.digest(pack)
    request = {
        "slug": "game", "function": {"name": "f1", "vram": VRAM, "target": baseline.linked_code.hex()},
        "compiler": compiler, "pack_sha256": digest, "header_sha256": hashlib.sha256(header.encode()).hexdigest(),
    }
    python_judge = hosted_judge.Packs(pack.parent, tmp_path / "python-cache")
    expected = [python_judge.judge({**request, "source": source}) for source in SOURCES]
    assert all("error" not in reply for reply in expected)
    assert [reply["score"] for reply in expected[:2]] == [0, 1]
    assert all(reply["score"] > 0 for reply in expected[2:4])
    assert expected[-1]["score"] is None

    tokens = {role: subprocess.run(gateway("add-user", "--dir", str(folder), "--name", role, "--role", role),
                                   capture_output=True, text=True, check=True).stdout.strip()
              for role in ("admin", "verifier")}
    server = subprocess.Popen(gateway("serve", "--dir", str(folder), "--listen", "127.0.0.1:0"),
                              stdout=subprocess.PIPE, text=True)
    try:
        base = f"http://127.0.0.1:{int(server.stdout.readline().split()[1])}"
        queue = RemoteJudge({"server": base, "token": tokens["admin"], "pack_sha256": digest}, request)
        # The normal worker still compiles. Forbid only the Python patcher so a fallback cannot pass the test.
        entry = (
            "from decompnet.rom import match\n"
            "from decompnet.app.cli import main\n"
            "def forbidden(*args):\n"
            "    raise AssertionError('queued MIPS relocations must be applied in Rust')\n"
            "match._patch = forbidden\n"
            "raise SystemExit(main())\n"
        )
        worker = subprocess.Popen(
            [str(mcp_gateway.built()), "verifier", "--server", base, "--token", tokens["verifier"],
             "--name", "rust", "--poll", "0.1", "--slots", "2", "--packs", str(tmp_path / "worker-packs"),
             "--parent-pipe", "--trials-only", "--judge", sys.executable, "--judge-arg", "-c",
             "--judge-arg", entry, "--judge-arg", "judge-worker"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            scores = queue.batch(SOURCES, lambda: None)
            assert scores == {i: reply["score"] for i, reply in enumerate(expected)}
        finally:
            worker.stdin.close()
            worker.wait(10)
        assert worker.returncode == 0, worker.stderr.read()
    finally:
        server.terminate()
        server.wait(5)
