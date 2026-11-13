"""The port readiness report (docs/PORTING.md, phase 0) on a homemade project: no game, no compiler."""
import hashlib
import json

import pytest

from decompnet import consoles
from decompnet.app import rom_explorer
from decompnet.app.engine import RomNetwork
from decompnet.app.tools import tool_names
from decompnet.consoles.n64.port import audio_library, microcode
from decompnet.network import NetworkError
from decompnet.port.readiness import describe, readiness

UCODE = (b"RSP Gfx ucode F3DEX       fifo 2.05  Yoshitaka Yasumoto 1998 Nintendo.\0"
         b"RSP Gfx ucode F3DEX.Rej   fifo 2.05  Yoshitaka Yasumoto 1998 Nintendo.\0"
         b"RSP Gfx ucode S2DEX       fifo 2.05  Yoshitaka Yasumoto 1998 Nintendo.\0"
         b"RSP Gfx ucode L3DEX       fifo 2.05  Yoshitaka Yasumoto 1998 Nintendo.\0")

# A matched function that reads data by an automatic name and by a chosen one, calls a function, and writes an
# address as a number (a comment's and a string's don't count).
GAME_C = """
void update(void) {
    /* 0x80001234 in a comment */
    D_80010000 += g_speed;
    *(s32 *)0x80020000 = 1;
    helper("0x80030000");
}
"""


@pytest.fixture
def project(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    image = bytes(64) + UCODE + bytes(64)
    (tmp_path / "game.z64").write_bytes(image)
    cfg = dict(kind="rom", platform="n64", rom=dict(file="game.z64", sha1=hashlib.sha1(image).hexdigest()),
               segments=[dict(name="main", rom_start=0x40, rom_end=0x100, vram=0x80000400)],
               compiler={"toolchain": "gcc-2.7.2-kmc", "flags": []})
    sdk = {"naming": {"method": "sdk_signature", "name": "osInvalDCache", "source_corpora": ["libultra"]}}
    synth = {"naming": {"method": "sdk_signature", "name": "alSynNew", "source_corpora": ["libultra"]}}
    voice = {"naming": {"method": "sdk_signature", "name": "_allocatePVoice", "source_corpora": ["libultra"]}}
    stray = {"naming": {"method": "sdk_signature", "name": "n_alResampleParam", "source_corpora": ["libultra"]}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                         ("game", "game", "mips", 1, str(tmp_path), json.dumps(cfg))).lastrowid
        for name, vram, status, source, meta in [
                ("update", 0x80000400, "matched", GAME_C, {}),
                ("helper", 0x80000480, "matched", "void helper(char *s) {}", {}),
                ("boot_stub", 0x80000500, "asm", None, {"asm_code": "hand-written assembly"}),
                ("draw_hud", 0x80000580, "open", None, {}),
                ("osInvalDCache", 0x80000600, "asm", None, sdk),
                ("alSynNew", 0x80000680, "matched", "void alSynNew(void) {}", synth),
                ("alSynDelete", 0x80000690, "matched", "void alSynDelete(void) {}", synth | {"naming": {
                    "method": "sdk_signature", "name": "alSynDelete", "source_corpora": ["libultra"]}}),
                ("_allocatePVoice", 0x800006A0, "matched", "void _allocatePVoice(void) {}", voice),
                # one game function whose code equals an n_audio one by chance doesn't make the game an n_audio game
                ("reset_callback", 0x800006B0, "matched", "void reset_callback(void) {}", stray),
                ("osLooksFamiliar", 0x80000700, "matched", "void osLooksFamiliar(void) {}", {})]:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,status,matched_source,meta) "
                       "VALUES(?,?,?,?,?,?,?,?)", (pid, name, 32, 8, vram, status, source, json.dumps(meta)))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "g_speed", 0x80010010, 4, "data"))
    yield net, tmp_path
    if hasattr(net, "autosave"):
        net.autosave.close()


def test_report_counts_what_stands_between_the_game_and_a_port(project):
    net, root = project
    before = [tuple(r) for r in net.store.all("SELECT * FROM functions")]
    with net.progress.view():
        report = readiness(net, "game")
    code = report["code"]
    assert report["kit"]["name"] == "libultraship and Torch"
    assert (code["total"], code["c"]) == (10, 7)
    # Library code is what the app proved, never a familiar name: osLooksFamiliar stays the game's own.
    assert (code["replaced"], code["native"]) == (1, 4)
    assert report["names"]["unproven"] == ["osLooksFamiliar"]
    assert report["names"]["game_asm"] == ["boot_stub"] and report["names"]["open"] == ["draw_hud"]
    assert report["data"]["symbols"] == 2 and report["data"]["automatic"] == 1
    assert report["addresses"] == {"functions": 1, "literals": 1, "examples": ["update"]}
    rom = report["rom_data"]
    assert rom["bytes"] == len((root / "game.z64").read_bytes()) - 0xC0 and not rom["scanned"]
    assert "ROM Explorer hasn't scanned it" in " ".join(report["blockers"])
    support = {(g["name"], g["variant"]): g["support"] for g in report["graphics"]}
    assert support == {("F3DEX", ""): "rendered", ("F3DEX", "Rej"): "rendered", ("S2DEX", ""): "check",
                       ("L3DEX", ""): "missing"}
    assert report["audio"]["library"] == "libultra's audio synthesizer"
    text = " ".join(report["blockers"])
    assert "L3DEX 2.05 microcode, which the kit doesn't render" in text and "S2DEX" in text
    assert "1 of the game's own functions are assembly" in text and not report["ready"]
    assert "boot_stub" in describe(report)
    assert [tuple(r) for r in net.store.all("SELECT * FROM functions")] == before  # it reads, never writes


def test_rom_explorer_scan_says_which_bytes_have_no_shape_yet(project, monkeypatch):
    net, _ = project
    regions = [{"kind": "code", "size": 192}, {"kind": "image", "size": 100}, {"kind": "unknown", "size": 40},
               {"kind": "repeated bytes", "size": 8}, {"kind": "audio", "size": 30}]
    monkeypatch.setattr(rom_explorer, "listing", lambda net, slug: {"available": True, "stale": True, "regions": regions})
    with net.progress.view():
        report = readiness(net, "game")
    rom = report["rom_data"]
    assert rom["scanned"] and rom["unshaped"] == 48
    assert rom["kinds"] == {"image": 100, "unknown": 40, "audio": 30, "repeated bytes": 8}
    assert any("can't yet say what 48 bytes" in b and "scan again" in b for b in report["blockers"])
    assert "image 100 bytes" in describe(report)


def test_a_console_without_a_kit_still_gets_the_counts(project, monkeypatch):
    net, _ = project
    monkeypatch.setattr(type(consoles.get("n64")), "port_kit", None)
    with net.progress.view():
        report = readiness(net, "game")
    assert report["kit"] is None and report["graphics"] == []
    assert report["code"]["replaced"] == 0 and report["code"]["library_other"] == 5
    assert report["blockers"][0].startswith("Nintendo 64 has no port kit yet")


def test_projects_that_are_not_rom_projects_are_told_why(project):
    net, root = project
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE slug='game'", (json.dumps({"kind": "fork", "platform": "gc"}),))
    with net.progress.view(), pytest.raises(NetworkError, match="split from a ROM"):
        readiness(net, "game")


def test_microcode_and_audio_readers():
    assert microcode(b"no signatures here") == []
    assert microcode(b"RSP SW Version: 2.0D, 04-01-96\0SGI U64 GFX SW TEAM") == [
        {"name": "Fast3D", "variant": "", "version": "2.0D", "support": "rendered"}]
    assert audio_library(["n_alSynNew", "n_alSynDelete"])["library"] == "libultra's n_audio synthesizer"
    assert audio_library(["MusInitialize", "MusSetMasterVolume"])["library"] == "libmus (MusPlayer)"
    assert audio_library(["MusInitialize", "MusSetMasterVolume", "alSynNew", "alInit", "n_alResampleParam"])[
        "library"] == "libmus (MusPlayer) over libultra's audio synthesizer"
    assert audio_library(["n_alSynNew"])["library"] == ""
    assert audio_library(["main"])["library"] == ""


def test_agents_get_the_tool_on_rom_projects_only():
    assert "port_readiness" in tool_names(fork=False)
    assert "port_readiness" not in tool_names(fork=True)


def test_the_kit_says_where_each_libultra_function_comes_from():
    """libultraship defines part of libultra (port.LIBULTRASHIP, read from its source at the commit Torch pins); the
    game's own math compiles for the PC; the rest of libultra's plumbing is the port's glue."""
    from decompnet.consoles.n64.port import LIBULTRASHIP, _sdk_group
    assert {_sdk_group(n) for n in ("osRecvMesg", "osPiStartDma", "osViSetMode", "memcpy", "sqrtf")} == {"replaced"}
    assert {_sdk_group(n) for n in ("guMtxIdent", "guOrthoF", "sins", "__cosf", "__osContDataCrc", "alSynNew")} == {
        "native"}
    assert {_sdk_group(n) for n in ("osSpTaskYielded", "__osLeoResume", "osRomBase", "__osPiAccessQueue")} == {"glue"}
    assert _sdk_group("osCreateThread") == "replaced"  # decompdecomp's glue for libultraship has threads
    assert _sdk_group("draw_hud") is None and "osCreateThread" not in LIBULTRASHIP


def test_the_boot_values_follow_the_games_country():
    from decompnet.consoles.n64.port import boot_globals
    tv = lambda code: boot_globals({"rom": {"game_code": code}})[0x80000300][3]
    assert (tv("NVLP"), tv("NSMJ"), tv("NSME"), tv("NABB"), tv("")) == (0, 1, 1, 2, 1)  # PAL, NTSC, NTSC, MPAL
    assert boot_globals({})[0x80000318][1:] == ("uint32_t", 4, 0x400000)  # osMemSize: 4 MB, no Expansion Pak


def test_the_play_step_offers_each_runtime_with_what_it_still_needs(project):
    """WebAssembly plays now; the native runtimes wait for the checklist's steps, and stay closed while
    decompdecomp can't build on them."""
    net, _ = project
    with net.progress.view():
        report = readiness(net, "game")
    runtimes = {r["id"]: r for r in report["runtimes"]}
    assert list(runtimes) == ["wasm", "libultraship", "n64modernruntime"]
    assert runtimes["wasm"]["playable"] and runtimes["wasm"]["missing"] == []
    assert runtimes["libultraship"]["missing"] == ["The game's code in C", "Compiles for a PC", "Links for a PC",
                                                   "Assets extracted"]
    assert "Assets extracted" not in runtimes["n64modernruntime"]["missing"]  # it reads the ROM as it runs
    assert not runtimes["libultraship"]["playable"] and not runtimes["n64modernruntime"]["playable"]


def test_a_native_runtime_is_closed_until_decompdecomp_builds_on_it():
    from decompnet.port.readiness import runtimes
    done = {"code": {"open": 0, "game_asm": 0}, "pc": {"failed": 0, "warned": 0},
            "link": {"undefined": 0, "duplicates": []},
            "assets": {"extracted": {"current": True, "mapped": 3, "total": 3}}}
    kit = consoles.get("n64").port_kit
    native = {r["id"]: r for r in runtimes(kit, done)}
    assert native["libultraship"]["missing"] == [] and not native["libultraship"]["playable"]
    pointers = done | {"pc": {"failed": 0, "warned": 4}}  # 64-bit ports lose half of each cast pointer
    assert runtimes(kit, pointers)[2]["missing"] == ["Compiles for a PC"]
    assert runtimes(None, done) == []
