"""N64Recomp from the project's split (consoles/n64/recomp.py, app/recompile.py): the symbols it's given, code found by
the calls into it, the pinned build, and a real run on a homemade ROM when the tool is installed (CI has none)."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app import recompile
from decompnet.app.engine import RomNetwork
from decompnet.consoles.n64 import recomp
from decompnet.network import NetworkError
from decompnet.port.readiness import describe, readiness

JR_RA, NOP = 0x03E00008, 0


def jal(target: int) -> int:
    return (3 << 26) | ((target >> 2) & 0x03FFFFFF)


def j(target: int) -> int:
    return (2 << 26) | ((target >> 2) & 0x03FFFFFF)


def homemade_rom() -> bytes:
    """A header whose entry point is 0x80000400, and code at 0x1000 (0x80000400): `entry` calls `listed`, which calls
    `unlisted` (the split doesn't know it) and jumps to a label of its own; `unlisted` returns 7."""
    words = {
        0x1000: [jal(0x80000420), NOP, JR_RA, NOP],                            # entry
        0x1020: [jal(0x80000440), NOP, j(0x80000430), NOP, JR_RA, NOP],       # listed (j to its own label)
        0x1040: [0x24020007, JR_RA, NOP],                                     # unlisted: li v0, 7
    }
    rom = bytearray(0x2000)
    rom[0:4] = bytes.fromhex("80371240")
    rom[8:12] = (0x80000400).to_bytes(4, "big")
    for at, ws in words.items():
        for i, w in enumerate(ws):
            rom[at + 4 * i:at + 4 * i + 4] = w.to_bytes(4, "big")
    return bytes(rom)


SEGMENTS = [{"name": "main", "rom_start": 0x1000, "rom_end": 0x1100, "vram": 0x80000400}]
SPLIT = [{"name": "entry", "vram": 0x80000400, "rom": 0x1000, "size": 0x10},
         {"name": "listed", "vram": 0x80000420, "rom": 0x1020, "size": 0x18}]


def test_code_the_split_doesnt_list_is_found_by_the_calls_into_it():
    found = recomp.discover(homemade_rom(), SEGMENTS, SPLIT, 0x80000400)
    assert [(f["name"], f["size"], f["why"]) for f in found] == [("func_80000440", 0xC, "called from listed")], \
        "the jump to listed's own label is no new function; unlisted ends after its jr $ra and delay slot"
    # a call into the body of a function the split lists is a label there
    assert recomp.discover(homemade_rom(), SEGMENTS, [{**SPLIT[0], "size": 0x60}], 0x80000400) == []


def test_a_call_into_a_guess_splits_it():
    rom = bytearray(homemade_rom())
    rom[0x1000:0x1004] = jal(0x80000440).to_bytes(4, "big")   # entry calls 0x440 and 0x420 (none listed)
    rom[0x1008:0x100C] = jal(0x80000420).to_bytes(4, "big")
    found = recomp.discover(bytes(rom), SEGMENTS, [SPLIT[0]], 0x80000400)
    assert [(f["vram"], f["size"]) for f in found] == [(0x80000420, 0x18), (0x80000440, 0xC)]


def test_the_symbols_file_and_config():
    text = recomp.symbols(SEGMENTS, SPLIT)
    assert 'name = "main"\nrom = 0x1000\nvram = 0x80000400\nsize = 0x100' in text
    assert '{ name = "listed", vram = 0x80000420, size = 0x18 }' in text
    cfg = recomp.config(recomp.Path("/x/rom.z64"), 0x80000400, ["bad"], ["atan"])
    assert 'entrypoint = 0x80000400' in cfg and 'stubs = ["bad"]' in cfg and 'renamed = ["atan"]' in cfg


def test_n64recomp_is_pinned_with_its_submodules():
    tc = toolchains.get("n64recomp")
    assert tc.build == "n64recomp" and tc.license == "MIT" and "cmake" in tc.requires
    assert [p["into"] for p in tc.parts] == ["source"] + [f"source/lib/{n}" for n in
                                                         ("rabbitizer", "ELFIO", "fmt", "tomlplusplus", "sljit")]
    assert all(len(p["sha256"]) == 64 and "windows-x86_64" not in p["platforms"] for p in tc.parts)


@pytest.fixture
def project(tmp_path):
    image = homemade_rom()
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    (tmp_path / "game.z64").write_bytes(image)
    cfg = dict(kind="rom", platform="n64", rom=dict(file="game.z64", sha1=hashlib.sha1(image).hexdigest()),
               segments=SEGMENTS, compiler={"toolchain": "gcc-2.7.2-kmc", "flags": []})
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                         ("game", "game", "mips", 1, str(tmp_path), json.dumps(cfg))).lastrowid
        for f in SPLIT:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,rom_offset,status,meta) "
                       "VALUES(?,?,?,?,?,?,?,?)", (pid, f["name"], f["size"], f["size"] // 4, f["vram"], f["rom"],
                                                   "open", "{}"))
    yield net, tmp_path
    if hasattr(net, "autosave"):
        net.autosave.close()


def test_readiness_shows_the_last_run(project):
    net, root = project
    with net.progress.view():
        assert readiness(net, "game")["recomp"] is None and readiness(net, "game")["recomp_available"]
    out = recomp.folder(root)
    out.mkdir(parents=True)
    (out / "recomp.json").write_text(json.dumps({
        "at": 1.0, "functions": 2, "found": [{"name": "func_80000440", "vram": 0x80000440, "size": 12,
                                              "why": "called from listed"}],
        "refused": [{"name": "listed", "why": "Unhandled instruction: lwc2"}], "renamed": [], "runtime": [],
        "errors": [], "error_count": 0, "between": [], "unusable": [], "files": 1, "folder": str(out)}))
    with net.progress.view():
        report = readiness(net, "game")
    assert report["recomp"]["stubs"] == 1 and report["recomp"]["found"] == 1
    assert report["recomp"]["discoveries"][0]["rom"] == 0x1040
    assert report["recomp"]["succeeded"] is None and report["recomp"]["total"] == 3
    text = describe(report)
    assert "Recompiled (static recompiler, last run): 2 of the split's functions and 1 the split doesn't list" in text
    assert "listed (Unhandled instruction: lwc2)" in text and "func_80000440 (called from listed)" in text


@pytest.mark.skipif(toolchains.installed("n64recomp") is None, reason="needs the n64recomp tool")
def test_recompile_writes_c_that_compiles(project):
    from decompnet.app import recompile_review

    net, root = project
    summary = recompile.run(net, "game", log=lambda *a: None)
    assert summary["functions"] == 2 and [f["name"] for f in summary["found"]] == ["func_80000440"]
    assert summary["refused"] == [] and summary["error_count"] == 0, summary
    assert summary["succeeded"] == 3
    text = "".join(p.read_text() for p in (recomp.folder(root) / "funcs").glob("*.c"))
    assert "RECOMP_FUNC void recomp_entrypoint(" in text and "RECOMP_FUNC void listed(" in text
    assert "func_80000440(rdram, ctx);" in text and "RECOMP_FUNC void func_80000440(" in text
    assert recomp.load(root)["files"] == summary["files"]

    _code_config(root)
    assert recompile_review.add(net, "game", summary["at"], [0x80000440], _review_log())["added"]
    next_summary = recompile.run(net, "game", log=lambda *a: None)
    assert next_summary["functions"] == 3 and not next_summary["found"]
    assert next_summary["error_count"] == 0


def _state(path, sp=0x80100000, args=(0, 0, 0, 0), fpr=None):
    gpr = [0] * 32
    gpr[29] = sp | 0xFFFFFFFF00000000
    for i, a in enumerate(args):
        gpr[4 + i] = a
    path.write_text("80000400 " + " ".join(f"{g:016X}" for g in gpr) + " 0 0 "
                    + " ".join(f"{w:08X}" for w in (fpr or [0] * 32)) + "\n")
    return path


@pytest.mark.skipif(toolchains.installed("n64recomp") is None, reason="needs the n64recomp tool")
def test_one_recorded_call_runs_through_the_recompiled_c(project, tmp_path):
    """The harness runs one recompiled function from a recorded state: `listed` calls the function the split didn't
    list, which returns 7 in $v0, and jumps to its own return."""
    net, root = project
    recompile.run(net, "game", log=lambda *a: None)
    program = recomp.harness(recomp.folder(root), 0x80000400, log=lambda *a: None)
    rdram = tmp_path / "in.rdram"
    rdram.write_bytes(bytes(4 << 20))
    result = recomp.run_one(program, 0x80000420, _state(tmp_path / "in.state"), rdram, tmp_path / "out")
    assert result["ok"] and result["v0"] == 7 and result["runtime"] == [], result
    assert (tmp_path / "out.rdram").stat().st_size == 4 << 20
    assert recomp.run_one(program, 0x80000999, tmp_path / "in.state", rdram, tmp_path / "x")["ok"] is False


@pytest.mark.skipif(toolchains.installed("n64recomp") is None, reason="needs the n64recomp tool")
def test_a_call_looping_on_a_runtime_stub_ends_with_a_reason_not_a_flood(tmp_path):
    """A function that waits for a message in a loop (osRecvMesg: a stub in the harness, which never gives one) once
    wrote its "runtime" line until it had filled 12 GB; it's said once and the call gives up."""
    rom = bytearray(homemade_rom())
    for i, w in enumerate([jal(0x80000480), NOP, j(0x80000460), NOP, JR_RA, NOP]):  # waits: forever
        rom[0x1060 + 4 * i:0x1064 + 4 * i] = w.to_bytes(4, "big")
    for i, w in enumerate([JR_RA, NOP]):  # osRecvMesg, which N64Recomp leaves to its runtime
        rom[0x1080 + 4 * i:0x1084 + 4 * i] = w.to_bytes(4, "big")
    (tmp_path / "game.z64").write_bytes(bytes(rom))
    split = SPLIT + [{"name": "waits", "vram": 0x80000460, "rom": 0x1060, "size": 0x18},
                     {"name": "osRecvMesg", "vram": 0x80000480, "rom": 0x1080, "size": 0x8}]
    out = tmp_path / "recomp"
    recomp.recompile(toolchains.binary("n64recomp"), tmp_path / "game.z64", SEGMENTS, split, out, log=lambda *a: None)
    program = recomp.harness(out, 0x80000400, log=lambda *a: None)
    rdram = tmp_path / "in.rdram"
    rdram.write_bytes(bytes(4 << 20))
    result = recomp.run_one(program, 0x80000460, _state(tmp_path / "in.state"), rdram, tmp_path / "out", timeout=60)
    assert not result["ok"] and "kept calling the runtime's osRecvMesg" in result["error"], result
    assert (tmp_path / "out.result").stat().st_size < 1000


@pytest.mark.parametrize("line,name", [
    ("funcs_15.c:8813:18: error: second parameter of 'main' (argument array) must be of type 'char **'", "main"),
    ("funcs_15.c:8813:6: error: second argument of 'main' should be 'char **' [-Werror=main]", "main"),
    ("funcs_1.c:12:6: error: conflicting types for 'sqrtf'", "sqrtf"),
])
def test_a_function_named_as_the_c_library_or_main_is_renamed_not_set_aside(line, name):
    """A racing game's first C function is its own `main`: N64Recomp wrote it as main(rdram, ctx), which C refuses,
    and the check set it aside as a stub, so the port had nothing to boot. Such a clash is renamed (main_recomp)."""
    from decompnet.consoles.n64.recomp import CONFLICT
    m = CONFLICT.search(line)
    assert m and next(g for g in m.groups() if g) == name

def test_port_jobs_wait_for_and_reuse_the_first_recompile(tmp_path, monkeypatch):
    folder = tmp_path / 'build' / 'recomp'
    built, waited, finish = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def output_ready(*_):
        calls.append(1)
        built.set()
        assert finish.wait(5)
        folder.mkdir()
        (folder / 'recomp.json').write_text('{}')

    monkeypatch.setattr(recompile, '_run', output_ready)
    recompiler = SimpleNamespace(folder=lambda _: folder, load=recomp.load)

    def read(log):
        with recompile.ready(None, 'game', {'root': tmp_path}, recompiler, log) as output:
            assert (output / 'recomp.json').is_file()

    with ThreadPoolExecutor(max_workers=2) as pool:
        play = pool.submit(read, lambda _: None)
        try:
            assert built.wait(5)
            behaviour = pool.submit(read, lambda _: waited.set())
            assert waited.wait(5)
            assert not behaviour.done()
        finally:
            finish.set()
        play.result(timeout=5)
        behaviour.result(timeout=5)

    assert len(calls) == 1


def test_port_job_can_cancel_while_waiting_for_recompile(tmp_path):
    folder = tmp_path / 'build' / 'recomp'
    waited = threading.Event()
    stop = threading.Event()

    def queued():
        with recompile.build_turn(folder, lambda _: waited.set(), stop.is_set):
            pytest.fail('Cancelled job entered the build directory')

    with ThreadPoolExecutor(max_workers=1) as pool, recompile.build_turn(folder, print):
        future = pool.submit(queued)
        assert waited.wait(5)
        stop.set()
        with pytest.raises(NetworkError, match='Stopped'):
            future.result(timeout=5)


def _code_config(root):
    (root / "splat.yaml").write_text(
        "segments:\n  - {name: main, type: code, start: 0x1000, vram: 0x80000400}\n  - [0x1100]\n")


def _review_log():
    def log(_):
        pass

    log.progress = lambda _: None
    log.check_cancel = lambda: None
    return log


def test_review_adds_only_selected_original_bytes_and_retains_them_on_reimport(project):
    from decompnet.app import recompile_review
    from decompnet.rom import split
    from decompnet.rom.export import _symbol_lines

    net, root = project
    image = homemade_rom()
    start, unselected = 0x80000440, 0x80000460
    discoveries = recomp.discover(image, SEGMENTS, SPLIT, 0x80000400)
    # The report can also contain poorly bounded guesses. Reviewing one leaves the others pending.
    discoveries += [{"name": "func_80000460", "vram": unselected, "rom": 0x1060, "size": 8,
                     "why": "called from another discovery"}]
    _code_config(root)
    out = recomp.folder(root)
    out.mkdir(parents=True)
    summary = {"at": 1.0, "found": discoveries, "image_sha1": hashlib.sha1(image).hexdigest()}
    (out / "recomp.json").write_text(json.dumps(summary))
    proj = net.project_row("game", sync=False)
    store = net.project_store(proj["id"])
    log = _review_log()

    assert [r["vram"] for r in recompile_review.findings(net, proj, summary)] == [start, unselected]
    assembly = recompile_review.preview(net, proj, 1.0, start)["assembly"]
    assert "80000440" in assembly and "jr" in assembly and "80000448" in assembly
    assert store.one("SELECT 1 FROM functions WHERE vram=?", start) is None
    with pytest.raises(NetworkError, match="report changed"):
        recompile_review.add(net, "game", 0.5, [start], log)
    with pytest.raises(NetworkError, match="absent"):
        recompile_review.add(net, "game", 1.0, [start, 0x80000500], log)
    assert store.one("SELECT 1 FROM functions WHERE vram=?", start) is None
    assert recompile_review.add(net, "game", 1.0, [start], log)["added"] == [
        {"name": "func_80000440", "vram": start}]
    added = store.one("SELECT * FROM functions WHERE vram=?", start)
    assert added["target"] == image[0x1040:0x104C] and added["status"] == "open"
    assert added["rom_offset"] == 0x1040 and added["size"] == 12
    assert store.one("SELECT 1 FROM functions WHERE vram=?", unselected) is None
    assert "func_80000440 = 0x80000440; // type:func size:0xC" in _symbol_lines(net, proj)
    assert recompile_review.add(net, "game", 1.0, [start], log)["added"] == []
    found = recompile_review.findings(net, proj, summary)
    assert found[0]["added"] == "func_80000440" and not found[1]["blocked"]

    original = [split.function_from_bytes(f["name"], image[f["rom"]:f["rom"] + f["size"]], f["rom"],
                                          f["vram"], "main") for f in SPLIT]
    result = SimpleNamespace(functions=original[:], segments=[split.Segment(**SEGMENTS[0])])
    recompile_review.keep_reviewed_on_reimport(net, proj, result, image, summary["image_sha1"], log)
    assert [(f.vram, f.size) for f in result.functions[len(original):]] == [(start, 12)]
    overlap = split.function_from_bytes("overlap", image[0x1044:0x104C], 0x1044, start + 4, "main")
    changed = SimpleNamespace(functions=original + [overlap], segments=result.segments)
    recompile_review.keep_reviewed_on_reimport(net, proj, changed, image, summary["image_sha1"], log)
    assert not any(f.vram == start for f in changed.functions)

    with store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,rom_offset,status,meta) "
                   "VALUES(?,?,?,?,?,?,?,?)", (proj["id"], "new_overlap", 8, 2, unselected + 4, 0x1064, "open", "{}"))
    assert "Overlaps" in recompile_review.findings(net, proj, summary)[1]["blocked"]
    with pytest.raises(NetworkError, match="Overlaps"):
        recompile_review.add(net, "game", 1.0, [unselected], log)


def test_review_can_add_a_function_outside_the_declared_code_ranges(project):
    """A game whose code splat filed as data (the range check used to block every such function)."""
    from decompnet.app import recompile_review

    net, root = project
    image = homemade_rom()
    start = 0x80000440
    (root / "splat.yaml").write_text(
        "segments:\n  - {name: main, type: code, start: 0x1000, vram: 0x80000400, subsegments: "
        "[[0x1000, asm], [0x1040, bin]]}\n  - [0x1100]\n")
    summary = {"at": 1.0, "found": recomp.discover(image, SEGMENTS, SPLIT, 0x80000400),
               "image_sha1": hashlib.sha1(image).hexdigest()}
    out = recomp.folder(root)
    out.mkdir(parents=True)
    (out / "recomp.json").write_text(json.dumps(summary))
    proj = net.project_row("game", sync=False)
    row = recompile_review.findings(net, proj, summary)[0]
    assert row["outside_range"] and not row["blocked"]
    assert recompile_review.add(net, "game", 1.0, [start], _review_log())["added"] == [
        {"name": "func_80000440", "vram": start}]
    assert net.project_store(proj["id"]).one("SELECT 1 FROM functions WHERE vram=?", start)
