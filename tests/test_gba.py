"""Game Boy Advance: the cartridge header, and the demo ROM's blank logo and valid checksum.

The header is what tells a GBA cartridge from anything else: an ARM branch at 0, the fixed 0x96 at 0xB2 and the
complement checksum at 0xBD over the title, game code, maker code and version. The tests below use synthetic
headers of that shape (no game's bytes) and the demo, which is built from the CC0 Demo Quest code and zlib.
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import consoles, toolchains
from decompnet.app.engine import RomNetwork
from decompnet.consoles.gba import GBA, candidates, header
from decompnet.consoles.gba import split as gba_split
from decompnet.forks import csource
from decompnet.rom import RomError

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "projects" / "demo-gba"
ROM = DEMO / "build" / "demoquest.gba"
ELF = DEMO / "build" / "demoq_gba.elf"
GAME = ROOT / "projects" / "demo-n64" / "original"
DEMO_SRC = ROOT / "projects" / "demo-rom" / "src"
ZLIB = ROOT / "projects" / "zlib-n64" / "original"
ZLIB_UNITS = ["adler32", "compress", "crc32", "deflate", "inflate", "infblock", "infcodes", "inffast", "inftrees",
              "infutil", "trees", "uncompr", "zutil"]
LOGO_BYTES = header.LOGO_END - header.LOGO_START
DEMO_MISSING = [tool for tool in ("agbcc", "arm-gnu") if toolchains.installed(tool) is None]
DEMO_MISSING += [] if ZLIB.exists() else ["zlib (projects/zlib-n64/prepare.py)"]
MILESTONE_MISSING = DEMO_MISSING + ([] if toolchains.installed("luvdis") else ["luvdis"])


def _entry_branch(target: int = header.BASE + 0xC0) -> bytes:
    """The ARM `b` at 0 that lands at `target`, as the header writes it."""
    offset = (target - (header.BASE + 8)) >> 2
    return struct.pack("<I", 0xEA000000 | (offset & 0xFFFFFF))


def _header(*, title: bytes = b"TEST GAME", code: bytes = b"ATGE", version: int = 3,
            entry: int = header.BASE + 0xC0, fixed: int = 0x96, valid_checksum: bool = True) -> bytes:
    """A real-shaped GBA header and some body after it, with a blank logo (never Nintendo's)."""
    data = bytearray(0x200)
    data[0:4] = _entry_branch(entry)
    data[header.TITLE_START:header.TITLE_END] = title.ljust(12, b"\0")[:12]
    data[header.CODE_START:header.CODE_END] = code
    data[header.MAKER_START:header.MAKER_END] = b"00"
    data[header.FIXED_START] = fixed
    data[header.VERSION] = version
    data[header.CHECKSUM] = header.checksum(data) if valid_checksum else 0
    return bytes(data)


def test_a_gba_rom_is_told_by_its_header_and_nothing_else_is(tmp_path):
    rom = tmp_path / "game.gba"
    rom.write_bytes(_header() + bytes(0x100))
    assert consoles.identify(rom) is GBA
    picked = GBA.read_game(rom)
    assert picked.platform == "gba" and picked.filename == "rom.gba" and not picked.big_endian
    info = picked.info
    assert (info["title"], info["game_code"], info["region"], info["version"]) == \
        ("TEST GAME", "ATGE", "North America", 3)
    assert info["entrypoint"] == header.BASE and info["startup"] == header.BASE + 0xC0
    assert info["size"] == 0x300 and info["compiler_guess"] == "AGBCC" and info["gp"] == 0


def test_a_header_that_does_not_check_out_is_not_a_gba_rom():
    good = _header()
    assert GBA.sniff(good, b"game.gba")
    broken = bytearray(good)
    broken[header.CHECKSUM] ^= 1  # the complement checksum no longer agrees
    assert not GBA.sniff(bytes(broken), b"game.gba")
    broken = bytearray(good)
    broken[header.FIXED_START] = 0  # the fixed 0x96 the boot check wants
    assert not GBA.sniff(bytes(broken), b"game.gba")
    broken = bytearray(good)
    broken[0:4] = struct.pack("<I", 0xEB00002E)  # a `bl`, not the unconditional `b` at 0
    assert not GBA.sniff(bytes(broken), b"game.gba")
    assert not GBA.sniff(good[:header.HEADER_BYTES - 1], b"game.gba")  # too short to hold the header
    assert not GBA.sniff(bytes(0x200), b"game.gba")  # not a cartridge at all
    with pytest.raises(RomError, match="Game Boy Advance"):
        GBA.read_game(bytes(0x200))


def test_the_checksum_follows_the_title_and_version():
    one = _header(title=b"FIRST GAME", code=b"ATGE", version=0)
    two = _header(title=b"SECOND GAME", code=b"ATGE", version=1)
    assert one[header.CHECKSUM] != two[header.CHECKSUM]
    assert all(GBA.sniff(value, b"game.gba") for value in (one, two))
    # a title changed without its checksum is refused
    changed = bytearray(one)
    changed[header.TITLE_START] ^= 0x20
    assert not GBA.sniff(bytes(changed), b"game.gba")


def test_gba_candidates_ignore_prologue_shaped_data_far_from_the_code():
    """#187, #188: a cartridge's graphics and sound are mostly bytes that decode as returns and prologues. Only
    starts in the region the first round's code reaches (or points into) may be functions; far data stays data."""
    rom = bytearray(0x10000)
    rom[0xC0:0xC4] = bytes.fromhex("00b500bd")  # the known function: push {lr}; pop {pc}
    rom[0xFE:0x100] = b"\0\0"  # padding, so the prologue after it starts a function
    rom[0x100:0x104] = bytes.fromhex("10b500bd")  # push {r4, lr}; pop {pc}
    rom[0x140:0x144] = bytes.fromhex("00bd7047")  # a return, then a leaf `bx lr`
    for off in range(0x4000, 0x8000, 0x20):  # prologue-shaped data far from any known code
        rom[off - 2:off] = b"\0\0"
        rom[off:off + 4] = bytes.fromhex("10b500bd")
    found = candidates.candidates(bytes(rom), [header.BASE + 0xC0])
    assert header.BASE + 0x100 in found
    assert header.BASE + 0x142 in found
    assert not any(address >= header.BASE + 0x4000 for address in found), \
        [hex(address) for address in found if address >= header.BASE + 0x4000]


def test_gba_candidates_keep_leaf_callbacks_in_a_table_known_code_loads():
    """A callback table's address is even, so the literal load alone doesn't look like a code pointer. Its entries
    are the pointers: a distant `movs r0, #1; bx lr` callback must stay a candidate even without a prologue."""
    rom = bytearray(0x5000)
    # Known function: ldr r0, [pc, #4]; ldr r0, [r0]; bx lr; nop, then the table's address as its literal.
    rom[0xC0:0xC8] = bytes.fromhex("014800687047c046")
    rom[0xC8:0xCC] = (header.BASE + 0x1000).to_bytes(4, "little")
    rom[0x1000:0x1004] = (header.BASE + 0x4001).to_bytes(4, "little")
    rom[0x4000:0x4004] = bytes.fromhex("01207047")  # movs r0, #1; bx lr (a leaf callback)
    found = candidates.candidates(bytes(rom), [header.BASE + 0xC0])
    assert header.BASE + 0x4000 in found


def test_the_gba_tools_are_pinned_free_builds():
    agbcc, luvdis = toolchains.get("agbcc"), toolchains.get("luvdis")
    assert agbcc.license == "GPL-2.0" and not agbcc.proprietary and agbcc.requires == ["arm-gnu"]
    assert all(part["sha256"] for part in agbcc.parts)
    assert luvdis.license == "MIT" and luvdis.commit and not luvdis.proprietary


@pytest.mark.parametrize("reason", [
    "luvdis (the GBA disassembler) ran out of memory on this ROM and was stopped",
    "luvdis (the GBA disassembler) didn't finish within 5 minutes on this ROM and was stopped",
])
def test_gba_round_2_keeps_the_first_round_when_luvdis_stops(tmp_path, monkeypatch, reason):
    """#187, #188: a later round that runs out of memory or time isn't the import's end: the functions the first
    round found are kept, and the log says what happened and what to do next."""
    rom = tmp_path / "game.gba"
    rom.write_bytes(bytes(0x400))

    def luvdis(rom_path, workdir, cfg_in, **kwargs):
        if cfg_in is None:
            return "the first round\n", [header.BASE + 0xC0]
        raise RomError(reason)

    monkeypatch.setattr(gba_split, "_luvdis", luvdis)
    monkeypatch.setattr(gba_split.candidates, "candidates",
                        lambda rom_bytes, known: [header.BASE + 0xC0, header.BASE + 0x100])
    said: list[str] = []
    asm, found = gba_split.find_functions(rom, tmp_path, log=said.append)
    assert (asm, found) == ("the first round\n", [header.BASE + 0xC0])
    logged = "\n".join(said)
    assert reason in logged
    assert "keeping the 1 functions found so far" in logged
    assert "Please report" not in logged


def test_gba_round_2_timeout_follows_the_first_pass(tmp_path, monkeypatch):
    """#188: on a ROM where the first pass takes most of five minutes, every later pass needs at least that long:
    the round-2 limit follows round 1's time instead of a fixed BATCH_SECONDS."""
    rom = tmp_path / "game.gba"
    rom.write_bytes(bytes(0x400))
    monkeypatch.setattr(gba_split, "BATCH_SECONDS", 0.2)
    timeouts: list[float] = []

    def luvdis(rom_path, workdir, cfg_in, **kwargs):
        if cfg_in is None:
            time.sleep(0.3)
            return "the first round\n", [header.BASE + 0xC0]
        timeouts.append(kwargs["timeout"])
        raise RomError("stop the test here")

    monkeypatch.setattr(gba_split, "_luvdis", luvdis)
    monkeypatch.setattr(gba_split.candidates, "candidates",
                        lambda rom_bytes, known: [header.BASE + 0xC0, header.BASE + 0x100])
    gba_split.find_functions(rom, tmp_path, log=lambda *_: None)
    assert timeouts and timeouts[0] >= 0.6 and timeouts[0] > gba_split.BATCH_SECONDS


def test_gba_round_2_tries_later_batches_after_an_empty_one(tmp_path, monkeypatch):
    """#188: one batch of data must not end the search when another batch is untried; a later one may hold code."""
    rom = tmp_path / "game.gba"
    rom.write_bytes(bytes(0x400))
    monkeypatch.setattr(gba_split, "BATCH_CANDIDATES", 2)
    calls: list[str] = []

    def luvdis(rom_path, workdir, cfg_in, **kwargs):
        if cfg_in is None:
            return "the first round\n", [header.BASE + 0xC0]
        calls.append(cfg_in.read_text(encoding="utf-8"))
        if len(calls) == 1:
            return "the first round\n", [header.BASE + 0xC0]  # this batch was data
        return "a later round\n", [header.BASE + 0xC0, header.BASE + 0x100]

    monkeypatch.setattr(gba_split, "_luvdis", luvdis)
    monkeypatch.setattr(gba_split.candidates, "candidates", lambda rom_bytes, known: [
        header.BASE + 0xC0, header.BASE + 0x100, header.BASE + 0x102,
        header.BASE + 0x104, header.BASE + 0x106,
    ])
    asm, found = gba_split.find_functions(rom, tmp_path, log=lambda *_: None)
    assert len(calls) == 2
    assert (asm, found) == ("a later round\n", [header.BASE + 0xC0, header.BASE + 0x100])


@pytest.mark.skipif(MILESTONE_MISSING, reason="needs " + ", ".join(MILESTONE_MISSING))
def test_a_big_rom_keeps_round_two_bounded_and_keeps_round_one_functions(tmp_path, monkeypatch):
    """#188: 16 MiB of padding that decodes as a prologue every 0x400 bytes must not become 16 MiB of candidates.
    The first round's functions stay, and the second round finishes instead of spending an hour on data."""
    _build_demo()
    demo = ROM.read_bytes()
    size = 16 << 20
    data = bytearray(size)
    data[:len(demo)] = demo
    for off in range(len(demo) + 0x1000, size - 4, 0x400):
        data[off - 2:off] = b"\0\0"  # padding, then a real-looking prologue and return
        data[off:off + 4] = bytes.fromhex("10b500bd")
    big = tmp_path / "big.gba"
    big.write_bytes(data)

    first_asm, first = gba_split._luvdis(big, tmp_path, None)
    extra = candidates.candidates(bytes(data), first)
    assert len(extra) < 500, f"{len(extra)} candidates for data that isn't code"

    real = gba_split._luvdis

    def cached(rom_path, workdir, cfg_in, **kwargs):
        return (first_asm, first) if cfg_in is None else real(rom_path, workdir, cfg_in, **kwargs)

    monkeypatch.setattr(gba_split, "_luvdis", cached)
    said: list[str] = []
    asm, found = gba_split.find_functions(big, tmp_path, log=said.append)
    assert set(first) <= set(found) and len(found) >= len(first)
    assert any(line.startswith("round 2:") for line in said), said


@pytest.mark.skipif(toolchains.installed("luvdis") is None, reason="needs luvdis")
def test_gba_luvdis_survives_data_that_decodes_as_stack_adjustments(tmp_path):
    """#187: `add sp, #508` halves of graphics data doubled luvdis's emulated stack each time until MemoryError.
    A bounded stack makes the run finish quickly, and the memory cap keeps a regression from filling the machine."""
    rom = bytearray(0x1000)
    rom[0xC0:0xC0 + 2 * 80] = bytes.fromhex("7fb0") * 80  # add sp, #508
    rom[0xC0 + 2 * 80:0xC0 + 2 * 80 + 2] = bytes.fromhex("00bd")  # pop {pc}
    game = tmp_path / "stack.gba"
    game.write_bytes(rom)
    functions = tmp_path / "provided.cfg"
    functions.write_text("thumb_func 0x080000C0\n", encoding="utf-8")
    asm, found = gba_split._luvdis(game, tmp_path, functions, timeout=60, memory=512 << 20)
    assert header.BASE + 0xC0 in found and asm


def test_gba_luvdis_memory_errors_and_timeouts_become_a_sentence(tmp_path, monkeypatch):
    """#187: a MemoryError in luvdis's traceback and a run stopped for time say what happened, instead of handing
    the traceback to the user."""
    monkeypatch.setattr(gba_split.toolchains, "ensure", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(gba_split.toolchains, "venv_bin", lambda venv, name: tmp_path / name)
    monkeypatch.setattr(gba_split.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 1, "", "Traceback ...\nMemoryError"))
    with pytest.raises(RomError, match="ran out of memory"):
        gba_split._luvdis(tmp_path / "game.gba", tmp_path, None)

    def stopped(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(gba_split.subprocess, "run", stopped)
    with pytest.raises(RomError, match="didn't finish within 5 minutes"):
        gba_split._luvdis(tmp_path / "game.gba", tmp_path, None, timeout=300)


@pytest.mark.skipif(DEMO_MISSING, reason="needs agbcc, the Arm GNU toolchain"
                                        + ("" if ZLIB.exists() else " and zlib (projects/zlib-n64/prepare.py)"))
def test_the_demo_rom_has_a_blank_logo_and_a_valid_header():
    """The demo is the only game in this repository: its logo is zeros (Nintendo's never is), and its header's
    checksum is the one its title and version give."""
    if not ROM.exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True, capture_output=True, text=True,
                       timeout=900)
    rom = ROM.read_bytes()
    assert len(rom) >= header.HEADER_BYTES
    assert rom[header.LOGO_START:header.LOGO_END] == bytes(LOGO_BYTES)
    assert rom[header.CHECKSUM] == header.checksum(rom)
    assert GBA.sniff(rom, ROM) and consoles.identify(ROM) is GBA
    assert header.branch_target(rom) == header.BASE + 0xC0
    info = GBA.read_game(ROM).info
    assert (info["title"], info["game_code"]) == ("DEMOQUEST", "ADQE")
    assert (DEMO / "build" / "demo.json").exists()


# ---------------------------------------------------------------------------------------------- the demo through the engine

def _build_demo() -> None:
    if not ROM.exists() or not ELF.exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True, capture_output=True, text=True,
                       timeout=900)


def _demo_functions() -> dict[str, int]:
    """The demo's real functions, from the symbols its build kept beside the stripped ROM."""
    with open(ELF, "rb") as f:
        return {s.name: s["st_value"] & ~1 for s in ELFFile(f).get_section_by_name(".symtab").iter_symbols()
                if s["st_info"]["type"] == "STT_FUNC" and s["st_value"]}


def _body(text: str, name: str, named: list[str]) -> str | None:
    """One function's definition only: csource can run past a K&R definition into the next function."""
    body = csource.get(text, name)
    if body is None:
        return None
    for other in named:
        if other == name:
            continue
        span = csource.find(body, other)
        if span is not None and span[0] > 0:
            body = body[:span[0]]
    return body


def _demo_symbols() -> dict[str, int]:
    return json.loads((DEMO / "build" / "demo.json").read_text())["symbols"]


def _no_preprocessor(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not re.match(r"\s*#\s*(?:include|pragma)\b", line))


def _game_context() -> str:
    """Game Quest's types and prototypes, without the typedefs the project's header already has."""
    text = _no_preprocessor((GAME / "game.h").read_text())
    return "\n".join(line for line in text.splitlines()
                     if not re.match(r"typedef (signed|unsigned) (char|short|int) (s8|u8|s16|u16|s32|u32);", line)
                     and not re.match(r"typedef float f32;", line) and not line.strip().startswith("#define NULL"))


def _name_demo(net, slug: str) -> None:
    """Give every function and datum of the demo its real name, as an agent works out names in a decomp."""
    proj = net.project_row(slug)
    real, symbols, table = _demo_functions(), _demo_symbols(), net.symbols(proj["id"])
    pairs = []
    for name, value in symbols.items():
        addr = value & ~1 if name in real else value
        if addr < 0x02000000 or not re.fullmatch(r"[A-Za-z_]\w*", name) or (name.startswith("__") and name not in real):
            continue
        current, offset = table.name_at(addr)
        if offset or current == name:
            continue
        row = net.store.one("SELECT 1 FROM functions WHERE project_id=? AND vram=?", proj["id"], addr)
        if name in real and row is None:
            continue  # the splitter didn't find it (B3's split/add can add it)
        pairs.append((current, name))
    if pairs:
        said = net.rename_symbols(net.local_agent("gba-names")[0], slug, pairs, "the demo's names")
        assert said.startswith("✅"), said


@pytest.fixture(scope="module")
def gba_project(tmp_path_factory):
    if MILESTONE_MISSING:
        pytest.skip("needs " + ", ".join(MILESTONE_MISSING))
    _build_demo()
    work = tmp_path_factory.mktemp("gba-project")
    net = RomNetwork(work / "app.db", work)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("gba-import")
    said = net.edit_context(agent, slug, "", _game_context(), "the demo's types")
    assert said.startswith("✅"), said
    _name_demo(net, slug)
    return net, slug


def test_the_demo_imports_and_its_functions_are_found(gba_project):
    net, slug = gba_project
    proj = net.project_row(slug)
    real = _demo_functions()
    symbols = _demo_symbols()
    rows = {r["vram"]: r for r in net.store.all("SELECT name, vram, status, meta FROM functions WHERE project_id=? "
                                               "ORDER BY vram", proj["id"])}
    found = set(real.values()) & set(rows)
    assert net.overview(slug)["platform"] == "gba"
    assert net.overview(slug)["compiler"]["toolchain"] == "agbcc"
    assert len(real) >= 120 and len(rows) >= 110
    assert len(found) >= 110 and not (set(rows) - set(real.values()))
    for name in ("abs_s32", "game_tick", "entity_spawn", "player_take_damage", "count_alive_entities"):
        assert real[name] in rows, name
    assert all(json.loads(row["meta"] or "{}").get("thumb") is True for row in rows.values())
    assert all(name in symbols and (symbols[name] & ~1) == addr for name, addr in real.items())


def _game_function(net, slug: str, name: str) -> tuple[str, str]:
    """(the project's name of a demo function, its real C with that name)."""
    proj = net.project_row(slug)
    addr = _demo_functions()[name]
    row = net.store.one("SELECT name FROM functions WHERE project_id=? AND vram=?", proj["id"], addr)
    assert row is not None, f"{name} wasn't found by the splitter"
    body = _body((GAME / "game.c").read_text(), name, list(_demo_functions()))
    assert body is not None, name
    return row["name"], re.sub(rf"\b{re.escape(name)}\b", row["name"], body)


def test_a_few_demo_functions_match_and_wrong_c_does_not(gba_project):
    net, slug = gba_project
    agent, _ = net.local_agent("gba-attempts")
    name, source = _game_function(net, slug, "abs_s32")
    wrong = source.replace("return v < 0 ? -v : v;", "return v < 0 ? -v : v + 1;")
    assert wrong != source
    result = net.attempt(agent, name, wrong, slug)
    assert not result["matched"] and result["score"] not in (None, 0), result["text"]
    net.release(agent, name, "", slug)
    for function in ("abs_s32", "game_tick", "entity_spawn", "player_take_damage"):
        name, source = _game_function(net, slug, function)
        result = net.attempt(agent, name, source, slug)
        assert result["matched"] and result["score"] == 0, (function, result["text"])


def _bodies(names: list[str]) -> dict[str, tuple[Path, str]]:
    """Each function's C and the file it lives in, from the demo's own sources."""
    paths = [GAME / "game.c", DEMO_SRC / "main.c", DEMO_SRC / "libc.c", DEMO / "gbart.c"]
    paths += [ZLIB / f"{unit}.c" for unit in ZLIB_UNITS]
    found: dict[str, tuple[Path, str]] = {}
    for path in paths:
        text = _no_preprocessor(path.read_text())
        for name in names:
            if name not in found and (body := _body(text, name, names)):
                found[name] = (path, body)
    return found


def test_splits_and_adds_fix_the_merged_and_missed_demo_functions(gba_project):
    """The same split/add/boundary calls agents and the GUI use, on the functions luvdis got wrong."""
    net, slug = gba_project
    proj = net.project_row(slug)
    agent, _ = net.local_agent("gba-editor")
    # exit's row runs on into clamp_s32: cut the real clamp_s32 out as a function of its own.
    said = net.split_function(agent, slug, "exit", "0x80004E0", "clamp_s32")
    assert said.startswith("✅ split:"), said
    # The rest are functions luvdis never started; add each after its neighbour (or before the first row).
    # An address outside any function is added against any open function; _call_via_r1 is asm (never matched).
    anchor = "_call_via_r1"
    for name, addr in (("_call_via_r0", 0x800013C), ("zError", 0x80054A8), ("rng_next", 0x800052C),
                       ("lerp", 0x800068C), ("deflate_stored", 0x8001504), ("inflate_blocks", 0x80022F4)):
        said = net.split_function(agent, slug, anchor, f"0x{addr:08X}", name)
        assert said.startswith("✅ new function"), (name, said)
    # A boundary report is the agents' other editor: its 2-byte check must accept this Tail and the user can dismiss it.
    said = net.request_boundary(agent, slug, "deflateInit2_", "0x8000B9E", "the literal pool after the function")
    assert said.startswith("Reported:"), said
    items = net.cfg(net.project_row(slug)).get("boundaries", [])
    rid = max(r["id"] for r in items if r["status"] == "open" and r["function"] == "deflateInit2_")
    net.resolve_boundary(slug, rid, False)
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name='clamp_s32'", proj["id"])
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name='zError'", proj["id"])


def test_all_demo_functions_with_their_own_c(gba_project):
    """The real measure: every function the splitter found and the demo has C for is submitted as its own C.

    It is not expected to be every function yet: the splitter merges a few functions into their neighbour (B3's
    split/add), and a few zlib functions need declarations this first header doesn't have. The counts are printed so
    a change that loses matches is visible."""
    net, slug = gba_project
    proj = net.project_row(slug)
    agent, _ = net.local_agent("gba-all")
    real = _demo_functions()
    rows = {r["vram"]: r for r in net.store.all("SELECT name, vram, status FROM functions WHERE project_id=?",
                                                proj["id"])}
    bodies = _bodies(list(real))
    main_prefix = """
typedef unsigned long uLong;
int compress(unsigned char *dest, uLong *destLen, const unsigned char *source, uLong sourceLen);
int uncompress(unsigned char *dest, uLong *destLen, const unsigned char *source, uLong sourceLen);
uLong crc32(uLong crc, const unsigned char *buf, unsigned int len);
uLong adler32(uLong adler, const unsigned char *buf, unsigned int len);
const char *zlibVersion(void);
extern const char save_magic[];
extern unsigned char save_buffer[512];
extern unsigned char packed_buffer[640];
extern volatile u32 debug_crc;
extern volatile s32 debug_status;
void save_game(void);
s32 load_game(void);
"""
    libc_prefix = """
typedef unsigned int size_t;
extern char heap[];
extern size_t heap_used;
extern int errno;
void *malloc(size_t n); void *memset(void *dst, int c, size_t n);
void *calloc(size_t count, size_t size); void free(void *p);
void *memcpy(void *dst, const void *src, size_t n); int memcmp(const void *a, const void *b, size_t n);
size_t strlen(const char *s); char *strcpy(char *dst, const char *src); void exit(int code);
"""
    gbart_prefix = """
typedef union { float f; int i; } bits;
unsigned int udivmod(unsigned int n, unsigned int d, unsigned int *rem);
"""
    prefixes = {DEMO_SRC / "main.c": main_prefix, DEMO_SRC / "libc.c": libc_prefix, DEMO / "gbart.c": gbart_prefix}
    matched, failed, found = [], [], 0
    for name, addr in sorted(real.items(), key=lambda kv: kv[1]):
        row = net.store.one("SELECT name, vram, status FROM functions WHERE project_id=? AND vram=?", proj["id"], addr)
        if row is None:
            failed.append((name, "not found by the splitter"))
            continue
        found += 1
        if name not in bodies:
            failed.append((name, "no C source (hand-written assembly helper)"))
            continue
        source_path, body = bodies[name]
        if source_path in prefixes:
            source = prefixes[source_path] + "\n" + body
        elif source_path.parent == ZLIB:
            header = _no_preprocessor((ROOT / "projects" / "zlib-n64" / "units" / f"{source_path.stem}.h").read_text())
            source = header + "\n" + body
        else:
            source = body
        source = re.sub(rf"\b{re.escape(name)}\b", row["name"], source)
        if row["status"] == "matched":
            matched.append(name)
            continue
        try:
            result = net.attempt(agent, row["name"], source, slug)
        except Exception as error:  # noqa: BLE001  a measurement: record why, keep going
            failed.append((name, f"attempt failed: {error}"))
            try:
                net.release(agent, row["name"], "", slug)
            except Exception:  # noqa: BLE001  (a rejection before a claim)
                pass
            continue
        if result.get("matched"):
            matched.append(name)
        else:
            failed.append((name, " | ".join((result.get("text") or "").splitlines()[:2])[:300]))
            try:
                net.release(agent, row["name"], "", slug)
            except Exception:  # noqa: BLE001  (the attempt already released it)
                pass
    not_found = sum(1 for _, why in failed if why == "not found by the splitter")
    no_source = sum(1 for _, why in failed if why.startswith("no C source"))
    found_failed = len(failed) - not_found - no_source
    print(f"\nGBA demo: {len(real)} real functions, {len(rows)} found by the splitter, {not_found} not found, "
          f"{no_source} hand-written (no C), {found - no_source} with C submitted: {len(matched)} matched, "
          f"{found_failed} not")
    for name, why in failed:
        print(f"  {name}: {why}")
    assert len(matched) >= 90
    assert len(matched) + found_failed + no_source + not_found == len(real)


@pytest.mark.skipif(toolchains.installed("agbcc") is None or toolchains.installed("arm-gnu") is None,
                    reason="needs agbcc (agbcc_arm) and the Arm GNU toolchain")
def test_the_arm_judge_matches_arm_code_too(tmp_path):
    """The demo's C is Thumb, but the judge handles ARM code: compile a small function with agbcc_arm, assemble it
    with the pinned Arm GNU as, and link it against its own bytes."""
    from decompnet.arch import arm
    from decompnet.rom.symbols import SymbolTable

    agbcc, armgnu = toolchains.require("agbcc"), toolchains.require("arm-gnu") / "bin"
    (tmp_path / "arm.c").write_text("int add(int a, int b) { return a + b; }\n", encoding="utf-8")
    runs = [
        [str(armgnu / "arm-none-eabi-cpp"), "-nostdinc", "-undef", "-I", str(agbcc / "include"), "arm.c", "-o", "arm.i"],
        [str(agbcc / "bin" / "agbcc_arm"), "-O2", "arm.i", "-o", "arm.s"],
        [str(armgnu / "arm-none-eabi-as"), "-mcpu=arm7tdmi", "-o", "arm.o", "arm.s"],
    ]
    for argv in runs:
        result = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    with open(tmp_path / "arm.o", "rb") as f:
        elf = ELFFile(f)
        code = elf.get_section_by_name(".text").data()
    target = arm.ArmTarget("add", header.BASE, code, thumb=False)
    result = arm.link((tmp_path / "arm.o").read_bytes(), "add", target, SymbolTable(), lambda *_: None)
    assert result.exact, result.problems


def test_the_export_rebuilds_the_demo(gba_project):
    """The export is the judge here: matched C and luvdis assembly must rebuild the demo byte for byte."""
    net, slug = gba_project
    result = net.export_rom(slug, log=lambda *_: None)
    print(f"export: identical={result['identical']} matched={result['matched']} functions={result['functions']}")
    assert result["identical"], result.get("log")
    assert result["matched"] >= 70
    assert not any("no longer matches" in why for _, why in result.get("fallback", []))


def test_the_permuter_uses_the_gcc_weights_for_agbcc():
    from decompnet.app.permute import weight_family

    assert weight_family({"compiler": {"toolchain": "agbcc"}}, None, False) == "gcc"
