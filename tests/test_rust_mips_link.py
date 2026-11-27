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

ARRAY = ("volatile int D_80002020[2] = {6, 7}; extern int callee(int); "
         "int f1(void) { return callee(D_80002020[1]); }")
POINTERS = ('char * volatile D_80002020[2] = {"ACE", "BOB"}; extern int callee(int); '
            'int f1(void) { return callee(D_80002020[1][0]); }')
VRAM = 0x80001100
DATA = 0x80002020
CALLEE = 0x80002200
STRINGS = 0x80002100


def audit_worker():
    """Check the full linker result on the exact same object before returning the queued verdict."""
    from decompnet.app.cli import main
    from decompnet.rom import verifier_link

    native = verifier_link.link_rom

    def audit(obj, target, symbols, read, exchange):
        hooks = (match.rom_linker, match.elf_preparer, match.instruction_linker)
        tokens = [hook.set(None) for hook in hooks]
        try:
            expected = match.link(obj, target.name, target, symbols, read)
        finally:
            for hook, token in zip(hooks, tokens):
                hook.reset(token)
        result = native(obj, target, symbols, read, exchange)
        assert result is not None, "Rust ROM linker declined the test object"
        assert result == expected, (result, expected)
        return result

    verifier_link.link_rom = audit
    return main()


@pytest.mark.skipif(mcp_gateway.built() is None, reason="the Rust gateway isn't built")
@pytest.mark.parametrize("toolchain", ["gcc-2.7.2-kmc", "ido-7.1"])
@pytest.mark.parametrize("kind", ["array", "pointers"])
def test_real_mips_objects_have_the_same_verdict_on_the_shared_queue(tmp_path, toolchain, kind):
    if not toolchains.installed(toolchain):
        pytest.skip(f"{toolchain} is not installed")
    net = game(tmp_path)
    proj = net.project_row("game", sync=False)
    flags = list(toolchains.get(toolchain).flags)
    compiler = {"toolchain": toolchain, "flags": flags}
    header = "typedef int s32;"
    source = ARRAY if kind == "array" else POINTERS
    changed_data = source.replace(", 7", ", 8") if kind == "array" else source.replace('"BOB"', '"BOX"')
    sources = [source, changed_data, source.replace("callee(D_", "callee(1 + D_"),
               source.replace("callee", "unknown_call"), "int f1( /* compile failure */"]
    built = compile_source(compiler_config(tmp_path, {"compiler": compiler}), header, source)
    assert built.ok, built.log

    # Create a small ROM from the compiler's output at these known symbol addresses.
    with net.store.tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (proj["id"], "callee", CALLEE, 4, "func"))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (proj["id"], "D_80002020", DATA, 8, "data"))
    target = match.RomTarget("f1", VRAM, b"", [])
    rom = bytearray(ROM)
    if kind == "array":
        rom[DATA - 0x80000000:DATA - 0x80000000 + 16] = bytes.fromhex("0000000600000007") + bytes(8)
    else:
        # The compilers emit the unnamed strings in different orders.
        if toolchain == "gcc-2.7.2-kmc":
            words, literals = (STRINGS + 4, STRINGS), b"BOB\0ACE\0"
        else:
            words, literals = (STRINGS, STRINGS + 4), b"ACE\0BOB\0"
        rom[DATA - 0x80000000:DATA - 0x80000000 + 16] = b"".join(w.to_bytes(4, "big") for w in words) + bytes(8)
        rom[STRINGS - 0x80000000:STRINGS - 0x80000000 + 16] = literals + bytes(8)

    def read(address, size):
        offset = address - 0x80000000
        return bytes(rom[offset:offset + size]) if 0x1000 <= offset <= offset + size <= 0x3000 else None

    baseline = match.link(built.obj, "f1", target, net.symbols(proj["id"]), read)
    assert baseline.linked_code and not baseline.problems
    low = next(r.addend for r in baseline.candidate.relocs if r.symbol == "D_80002020" and r.type == 6)
    symbols = {"callee": CALLEE, "D_80002020": DATA + low}
    relocs = [(r.offset, r.type, symbols[r.symbol]) for r in baseline.candidate.relocs]
    assert {kind for _, kind, _ in relocs} >= {4, 5, 6}
    if toolchain == "gcc-2.7.2-kmc":
        assert low == 4
    rom[VRAM - 0x80000000:VRAM - 0x80000000 + len(baseline.linked_code)] = baseline.linked_code
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
    expected = [python_judge.judge({**request, "source": source}) for source in sources]
    assert all("error" not in reply for reply in expected)
    assert [reply["score"] for reply in expected[:2]] == [0, 1], expected
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
        entry = "from tests.test_rust_mips_link import audit_worker; raise SystemExit(audit_worker())"
        worker = subprocess.Popen(
            [str(mcp_gateway.built()), "verifier", "--server", base, "--token", tokens["verifier"],
             "--name", "rust", "--poll", "0.1", "--slots", "2", "--packs", str(tmp_path / "worker-packs"),
             "--parent-pipe", "--trials-only", "--judge", sys.executable, "--judge-arg", "-c",
             "--judge-arg", entry, "--judge-arg", "judge-worker"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            scores = queue.batch(sources, lambda: None)
        finally:
            worker.stdin.close()
            worker.wait(10)
        diagnostics = worker.stderr.read()
        assert worker.returncode == 0, diagnostics
        assert scores == {i: reply["score"] for i, reply in enumerate(expected)}, diagnostics
    finally:
        server.terminate()
        server.wait(5)
