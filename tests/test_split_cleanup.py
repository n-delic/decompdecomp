"""split_function cleanup: when a function is split, the head's extent changes, so its old best attempt no longer
describes it (reset best_source, not just best_score); and a head trimmed down to pure NOP padding is not a function
to match, so it is marked done ('asm') rather than left as an open sliver agents keep claiming."""

import shutil

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork

from .test_rom import built  # noqa: F401  (fixture that builds the demo ROM)

pytestmark = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None or not shutil.which("ld.lld"),
                                reason="needs gcc-2.7.2-kmc and ld.lld to build the demo ROM")

SLUG = "demo-quest-64"
FN = "func_800007C4"


@pytest.fixture
def net(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    assert net.import_rom(built, confirmed=True, log=lambda *_: None) == SLUG
    return net


def test_split_resets_the_heads_stale_best(net):
    agent, _ = net.local_agent("tester")
    pid = net._function(FN, SLUG)["project_id"]
    with net.store.tx() as db:  # an earlier agent's best attempt for the whole function
        db.execute("UPDATE functions SET best_source=?, best_score=? WHERE project_id=? AND name=?",
                   ("int stale(void){return 0;}", 123, pid, FN))
    net.split_function(agent, SLUG, FN, "0x800007D0")
    head = net._function(FN, SLUG)
    assert head["best_source"] is None        # the extent changed -> the old best no longer describes it
    assert head["best_score"] is None


def test_split_marks_a_pure_padding_head_done(net):
    agent, _ = net.local_agent("tester")
    pid = net._function(FN, SLUG)["project_id"]
    unit = net._function(FN, SLUG)["unit"]
    vram, rom = 0x80010000, 0x10000
    lines = "".join(f"/* {rom + 4 * i:X} {vram + 4 * i:08X} 00000000 */  nop\n" for i in range(3))
    asm = f"glabel padfn\n{lines}endlabel padfn\n"
    with net.store.tx() as db:  # three alignment NOPs, as the analysis sometimes glues ahead of a real function
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                   "status,best_source,best_score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, "padfn", 12, 3, b"\x00" * 12, "[]", asm, None, unit, vram, rom, "open",
                    "int was_here(void){return 1;}", 400))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "padfn", vram, 12, "func"))
    net._syms.pop(pid, None)  # the symbol cache predates the row we just inserted
    net.split_function(agent, SLUG, "padfn", f"0x{vram + 8:08X}")   # head = the leading NOPs
    head = net._function("padfn", SLUG)
    assert head["status"] == "asm", head["status"]   # pure padding -> marked done, out of the open pool
    assert head["best_source"] is None               # and its stale best cleared


def _insert_padding(net):
    """Insert 'padfn' = three alignment NOPs carrying a stale best; return its vram."""
    pid = net._function(FN, SLUG)["project_id"]
    unit = net._function(FN, SLUG)["unit"]
    vram, rom = 0x80010000, 0x10000
    lines = "".join(f"/* {rom + 4 * i:X} {vram + 4 * i:08X} 00000000 */  nop\n" for i in range(3))
    asm = f"glabel padfn\n{lines}endlabel padfn\n"
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                   "status,best_source,best_score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, "padfn", 12, 3, b"\x00" * 12, "[]", asm, None, unit, vram, rom, "open",
                    "int was_here(void){return 1;}", 400))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "padfn", vram, 12, "func"))
    net._syms.pop(pid, None)  # the symbol cache predates the row we just inserted
    return vram


def test_both_padding_halves_are_retired(net):
    # Splitting the three-NOP function at vram+8 leaves a two-NOP head and a one-NOP tail: both are pure alignment
    # padding, so both are retired and the reply offers neither as work (the review's case: the tail used to stay
    # open and the reply pointed the user straight at a lone NOP).
    agent, _ = net.local_agent("tester")
    vram = _insert_padding(net)
    reply = net.split_function(agent, SLUG, "padfn", f"0x{vram + 8:08X}")
    assert net._function("padfn", SLUG)["status"] == "asm"          # the head sliver retired...
    assert net._function("func_80010008", SLUG)["status"] == "asm"  # ...and the tail sliver too
    assert "both" in reply.lower() and "set aside" in reply.lower(), reply
    assert "Continue with" not in reply, reply                      # neither dead sliver is offered


def test_padding_head_shows_alignment_reason(net):
    from decompnet.network import set_aside
    agent, _ = net.local_agent("tester")
    vram = _insert_padding(net)
    net.split_function(agent, SLUG, "padfn", f"0x{vram + 8:08X}")
    aside = set_aside(net._function("padfn", SLUG))
    assert "padding" in aside["set_aside"].lower(), aside   # a real reason, not the
    assert aside["set_aside"] != "hand-written assembly"    # generic fallback


JR_RA = 0x03E00008  # jr $ra: a real MIPS instruction (non-zero), not padding


def _insert_words(net, name, words):
    """Insert a function `name` whose instructions are the given 32-bit `words`, carrying a stale best; return vram."""
    pid = net._function(FN, SLUG)["project_id"]
    unit = net._function(FN, SLUG)["unit"]
    vram, rom = 0x80010000, 0x10000
    dis = {0x00000000: "nop", JR_RA: "jr $ra"}
    lines = "".join(f"/* {rom + 4 * i:X} {vram + 4 * i:08X} {w:08X} */  {dis[w]}\n" for i, w in enumerate(words))
    asm = f"glabel {name}\n{lines}endlabel {name}\n"
    target = b"".join(w.to_bytes(4, "big") for w in words)
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                   "status,best_source,best_score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, name, 4 * len(words), len(words), target, "[]", asm, None, unit, vram, rom, "open",
                    "int was_here(void){return 1;}", 400))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, name, vram, 4 * len(words), "func"))
    net._syms.pop(pid, None)  # the symbol cache predates the row we just inserted
    return vram


def test_split_marks_a_pure_padding_tail_done(net):
    from decompnet.network import set_aside
    agent, _ = net.local_agent("tester")
    vram = _insert_words(net, "mixfn", [JR_RA, 0x0, 0x0])    # a real instruction, then two trailing NOPs
    reply = net.split_function(agent, SLUG, "mixfn", f"0x{vram + 4:08X}")  # tail = the trailing NOPs
    tail = net._function("func_80010004", SLUG)
    assert tail["status"] == "asm", tail["status"]          # the pure-padding tail is retired...
    assert "padding" in set_aside(tail)["set_aside"].lower(), set_aside(tail)
    assert net._function("mixfn", SLUG)["status"] != "asm"  # ...the real head stays workable
    assert "continue with mixfn" in reply, reply            # and the reply keeps the user on the head


def test_padding_head_only_points_at_tail(net):
    agent, _ = net.local_agent("tester")
    vram = _insert_words(net, "mixfn", [0x0, 0x0, JR_RA])    # two leading NOPs, then real code
    reply = net.split_function(agent, SLUG, "mixfn", f"0x{vram + 8:08X}")  # head = the leading NOPs
    assert net._function("mixfn", SLUG)["status"] == "asm"          # the padding head is retired...
    assert net._function("func_80010008", SLUG)["status"] != "asm"  # ...the real tail stays workable
    assert "func_80010008" in reply, reply                          # and the reply sends the user there
