"""A different program costs more than a different schedule (scoring.PENALTY_MEANING).

Operand differences used to cost 10 whatever they were, less than one reorder (60), so a saved best that stored one
field twice and never wrote the next (an N64 game's display-list function: 1825) outranked correct sources one
reorder away. A memory access at another offset or symbol, another constant or another call now costs 150 when the
other side has no such instruction anywhere; registers, branch labels, stack slots and stores in another order stay
cheap. Every processor the scorer reads: MIPS, PowerPC, x86 and ARM.
"""
import struct

import pytest

from decompnet import scoring
from decompnet.elfobj import FunctionObj
from decompnet.scoring import Insn, _classify


def insn(mnemonic, operands):
    return Insn(0, mnemonic, operands, f"{mnemonic} {operands}")


@pytest.mark.parametrize("a, b, penalty", [
    (("sw", "$t0, 0x20($v0)"), ("sw", "$t0, 0x28($v0)"), scoring.PENALTY_MEANING),  # another field
    (("addiu", "$a0, $a0, 4"), ("addiu", "$a0, $a0, 8"), scoring.PENALTY_MEANING),  # another constant
    (("jal", "func_80001000"), ("jal", "func_80002000"), scoring.PENALTY_MEANING),  # another call
    (("lui", "$at, %hi(D_8001)"), ("lui", "$at, %hi(D_8002)"), scoring.PENALTY_MEANING),  # another global
    (("lwz", "r3, 0x20(r4)"), ("lwz", "r3, 0x24(r4)"), scoring.PENALTY_MEANING),  # PowerPC
    (("mov", "eax, dword ptr [ecx + 0x20]"), ("mov", "eax, dword ptr [ecx + 0x28]"), scoring.PENALTY_MEANING),  # x86
    (("ldr", "r0, [r2, #0x10]"), ("ldr", "r0, [r2, #0x14]"), scoring.PENALTY_MEANING),  # ARM
    (("bl", "<foo>"), ("bl", "<bar>"), scoring.PENALTY_MEANING),
    (("sw", "$t0, 0x20($v0)"), ("sw", "$t1, 0x20($v0)"), scoring.PENALTY_REGALLOC),
    (("beq", "$a0, $zero, 0x40"), ("beq", "$a0, $zero, 0x48"), scoring.PENALTY_OPERAND),  # a target moves
    (("blt", "cr0, 0x40"), ("blt", "cr0, 0x48"), scoring.PENALTY_OPERAND),
    (("jne", ".L40"), ("jne", ".L48"), scoring.PENALTY_OPERAND),
    (("b", "#0x108"), ("b", "#0x110"), scoring.PENALTY_OPERAND),
    (("sw", "$s0, 0x10($sp)"), ("sw", "$s1, 0x14($sp)"), scoring.PENALTY_OPERAND),  # a spill slot
    (("mov", "eax, dword ptr [esp + 0x10]"), ("mov", "ecx, dword ptr [esp + 0x14]"), scoring.PENALTY_OPERAND),
])
def test_what_an_operand_difference_costs(a, b, penalty):
    assert _classify(insn(*a), insn(*b))[0] == penalty


def test_a_meaning_change_costs_more_than_a_reorder():
    assert scoring.PENALTY_MEANING > scoring.PENALTY_REORDER > scoring.PENALTY_OPERAND


def mips(*words):
    return FunctionObj("f", "mips", True, b"".join(struct.pack(">I", w) for w in words))


def sw(rt, offset, base=2):
    return (0x2B << 26) | (base << 21) | (rt << 16) | offset


JR_RA, NOP = 0x03E00008, 0


def test_the_display_list_case_a_field_written_twice_scores_worse_than_stores_in_another_order():
    """The saved best wrote the second rectangle's first word to c+0x20 again instead of c+0x28: once 10 (below a
    reorder), now a meaning change. The same stores in another order are a schedule, and stay cheap."""
    target = mips(sw(8, 0x20), sw(9, 0x24), sw(10, 0x28), sw(11, 0x2C), JR_RA, NOP)
    twice = mips(sw(8, 0x20), sw(9, 0x24), sw(10, 0x20), sw(11, 0x2C), JR_RA, NOP)
    swapped = mips(sw(10, 0x28), sw(9, 0x24), sw(8, 0x20), sw(11, 0x2C), JR_RA, NOP)
    broken, reordered = scoring.diff(target, twice), scoring.diff(target, swapped)
    assert broken.score >= scoring.PENALTY_MEANING
    assert reordered.score < scoring.PENALTY_REORDER  # (rows paired by opcode: two registers and two offsets)
    assert reordered.score < broken.score


def test_a_different_constant_on_x86_powerpc_and_arm():
    for target, wrong in [
        (FunctionObj("f", "x86", False, bytes.fromhex("8b4120c3")), FunctionObj("f", "x86", False, bytes.fromhex("8b4128c3"))),
        (FunctionObj("f", "ppc", True, struct.pack(">II", (32 << 26) | (3 << 21) | (4 << 16) | 0x20, 0x4E800020)),
         FunctionObj("f", "ppc", True, struct.pack(">II", (32 << 26) | (3 << 21) | (4 << 16) | 0x24, 0x4E800020))),
        (FunctionObj("f", "arm", False, struct.pack("<II", 0xE5920010, 0xE12FFF1E)),
         FunctionObj("f", "arm", False, struct.pack("<II", 0xE5920014, 0xE12FFF1E))),
    ]:
        assert scoring.diff(target, wrong).score == scoring.PENALTY_MEANING, target.arch


class _Store:
    def __init__(self, rows):
        import sqlite3
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE functions(id, project_id, name, vram, status, best_source, best_score)")
        self.conn.executemany("INSERT INTO functions VALUES(?,?,?,?,?,?,?)", rows)

    def all(self, sql, *args):
        return self.conn.execute(sql, args).fetchall()

    def tx(self):
        return self.conn


class _Pool:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def map(self, f, items):
        return map(f, items)


def _net(cfg, rows, scores):
    from types import SimpleNamespace
    net = SimpleNamespace(store=_Store(rows), cfg_value=cfg)
    net.cfg = lambda row: net.cfg_value
    net.project_store = lambda _id: net.store
    net._source_of = lambda fn: fn["best_source"]
    net._judge_with = lambda row, fn, tc, flags, source: (False, SimpleNamespace(score=scores[source]), "")
    net._pool = SimpleNamespace(group=lambda *_: _Pool())
    net._update_cfg = lambda slug, change, synchronize=True: change(net.cfg_value)
    return net


def test_saved_bests_scored_under_old_penalties_are_rescored_once(monkeypatch):
    """A best saved at 1825 under the old penalties is 2525 today: until it's rescored, a correct source at 2000
    can't replace it. Check again rescores each open best once and records the version."""
    from decompnet.app import score_version
    monkeypatch.setattr("decompnet.project.compiler_for", lambda cfg, vram, fn: {"toolchain": "t", "flags": []})
    rows = [(1, 6, "a", 0, "open", "broken", 1825), (2, 6, "b", 0, "open", "fine", 400),
            (3, 6, "c", 0, "matched", "done", 0)]
    net = _net({"kind": "rom"}, rows, {"broken": 2525, "fine": 400})
    row = {"id": 6, "slug": "game"}
    assert score_version.stale(net, row)
    assert score_version.refresh(net, row, log=lambda *_: None) == 1
    assert [r["best_score"] for r in net.store.all("SELECT best_score FROM functions ORDER BY id")] == [2525, 400, 0]
    assert net.cfg_value["score_version"] == scoring.SCORE_VERSION
    assert not score_version.stale(net, row) and score_version.refresh(net, row) == 0


def test_fork_projects_keep_objdiffs_scores():
    from decompnet.app import score_version
    assert not score_version.stale(_net({"kind": "fork"}, [], {}), {"id": 6, "slug": "game"})


def test_byte_identical_code_scores_zero_whatever_it_stores():
    """The penalty only weighs differences: a function that stores twice to one address (a hardware register, a
    display list) and matches is a match, score 0."""
    code = mips(sw(8, 0x20, base=1), sw(9, 0x20, base=1), sw(9, 0x20, base=1), JR_RA, NOP)
    result = scoring.diff(code, mips(sw(8, 0x20, base=1), sw(9, 0x20, base=1), sw(9, 0x20, base=1), JR_RA, NOP))
    assert result.score == 0 and result.bytes_match


def test_arm_r1_is_not_a_stack_slot():
    """r1 is PowerPC's stack pointer only: through ARM's r1 an offset is a field, scored as one."""
    ldr = lambda off: insn("ldr", f"r0, [r1, #{off}]")  # noqa: E731
    assert _classify(ldr("0x10"), ldr("0x14"), "arm")[0] == scoring.PENALTY_MEANING
    assert _classify(ldr("0x10"), ldr("0x14"), "thumb")[0] == scoring.PENALTY_MEANING
    # a plain number change through r1 (no meaning change possible: same mnemonic, number only) is no stack diff
    add = lambda n: insn("add", f"r0, r1, #{n}")  # noqa: E731
    assert _classify(add(4), add(8), "arm")[0] != scoring.PENALTY_STACKDIFF
    # ARM's real stack pointer keeps the cheap score
    sp = lambda off: insn("str", f"r0, [sp, #{off}]")  # noqa: E731
    assert _classify(sp("0x10"), sp("0x14"), "arm")[0] == scoring.PENALTY_STACKDIFF


def test_ppc_r1_stays_a_stack_slot():
    lwz = lambda off: insn("lwz", f"r0, {off}(r1)")  # noqa: E731
    assert _classify(lwz("0x10"), lwz("0x14"), "ppc")[0] == scoring.PENALTY_STACKDIFF
    assert _classify(lwz("0x10"), lwz("0x14"), "ppc64")[0] == scoring.PENALTY_STACKDIFF
