"""What compile_and_diff's diff shows agents. In one project every long result was cut ("N more rows not shown", N 27
to 181) and the only way to see the rest was another compile; and a target row loading a float constant by address
(`mulss xmm0, [_D_00412340]`) didn't say which constant, so agents spent read_data calls (or compiles) finding out."""

import struct

import pytest

from decompnet import scoring
from decompnet.app.forkengine import with_constant_values
from decompnet.arch import x86
from decompnet.network import NetworkError

from .test_claims import _game


def _rows(n):
    return [("|", f"add eax, {i}", f"add eax, {i + 1}") for i in range(n)]


def test_a_cut_diff_says_last_diff_shows_the_rest():
    text = scoring.render_rows(_rows(200))
    assert "more rows not shown: fix these first; last_diff(function) shows every row" in text
    assert "last_diff" not in scoring.render_rows(_rows(5))


def test_a_diff_says_what_differs_once_registers_and_order_are_set_aside():
    """On an Xbox racing game's near misses, more than half had real differences past the 60 rows a diff shows,
    behind rows that only differed by register; and a quarter differed only by registers and order, which wants
    another approach than code that's missing."""
    rows = [("r", f"mov eax, [ecx+{4 * i}]", f"mov edx, [ecx+{4 * i}]") for i in range(80)]
    rows += [("|", "movaps xmm1, xmm2", "mov edi, edi"), ("<", "nop", ""),  # padding follows from code before it
             ("i", "movss xmm0, [_D_00412340]", "movss xmm0, [?g_scale@@3MA]   ; target's D_00412340 = 0.5f"),
             ("i", "mov eax, [esp+0x18]", "mov eax, [esp+0x1c]"), ("|", "jne 0x10d0", "jne 0x10e4")]
    text = scoring.render_rows(rows)
    second = text.splitlines()[1]
    assert second.startswith("Registers, stack slots and order aside, what still differs: the target has "
                             "`movaps xmm1, xmm2`; yours has `mov edi, edi`.")
    assert "more rows not shown" in text and "movaps" not in "\n".join(text.splitlines()[2:])  # past the cut
    # nothing but registers, order, names and stack slots
    same = [("r", "lw $v0, 0x54($sp)", "lw $v1, 0x58($sp)"), ("o", "addu $a0, $a0, $v0", "move $s0, $a0"),
            ("o", "move $s0, $a0", "addu $a0, $a0, $v0"), ("i", "lwz r3, 0x10(r1)", "lwz r4, 0x14(r1)")]
    assert scoring.residue(same) == ([], [])
    assert "your instructions are the target's" in scoring.render_rows(same)
    # a frame's size isn't a stack slot: it shows
    assert scoring.residue([("i", "sub esp, 0xc", "sub esp, 0x10")]) == (["sub esp, 0xc"], ["sub esp, 0x10"])
    assert scoring.residue_line([(" ", "ret", "ret")]) == ""
    assert scoring.shape("call ?Foo@@YAXXZ") == scoring.shape("call _func_00011130@8") == "call S"
    assert scoring.shape("mov ecx, ?g_state@@3UState@@A+0x1946") == scoring.shape("mov edx, _D_0073BACA")


@pytest.mark.parametrize("cs_arch,cs_modes,target,yours,same", [
    # x86: a prefix doesn't hide the opcode after it
    ("X86", ["32"], "f00108", "f02908", False),  # lock add vs lock sub
    ("X86", ["32"], "f3a5", "f3a4", False),  # rep movsd vs rep movsb
    # x86: an index's scale in a stack slot is an element's size, not the frame's layout
    ("X86", ["32"], "8b448c18", "8b44cc18", False),  # [esp + ecx*4 + 0x18] vs [esp + ecx*8 + 0x18]
    ("X86", ["32"], "8b448c18", "8b548c1c", True),  # another register and slot: the same
    # ARM: a shift in a stack slot likewise; another offset is the frame's
    ("ARM", ["ARM"], "02019de7", "82019de7", False),  # [sp, r2, lsl #2] vs lsl #3
    ("ARM", ["ARM"], "02019de7", "42019de7", False),  # [sp, r2, lsl #2] vs asr #2: another shift
    ("ARM64", ["ARM"], "e05b61b8", "e0db61b8", False),  # [sp, w1, uxtw #2] vs sxtw #2: another extend
    ("ARM", ["ARM"], "010cc1e3", "020cc1e3", False),  # bic r0, r1, #0x100 vs #0x200: a mask, not a branch target
    ("ARM", ["ARM"], "010cc103", "020cc103", False),  # biceq likewise
    ("ARM64", ["ARM"], "007d20d4", "207d20d4", False),  # brk #0x3e8 vs #0x3e9: a breakpoint's number
    # a branch's target (ARM's with `#`) moves with the code before it
    ("ARM", ["ARM"], "400000ea", "410000ea", True),  # b #0x1108 vs b #0x110c
    ("ARM", ["ARM"], "4000000a", "4100000a", True),  # beq
    ("ARM64", ["ARM"], "40000014", "41000014", True),  # b #0x1100 vs b #0x1104
    ("ARM64", ["ARM"], "00081836", "20081836", True),  # tbz w0, #3, #0x1100 vs #0x1104
    ("ARM64", ["ARM"], "000800b4", "200800b4", True),  # cbz x0
    ("X86", ["32"], "f2f00108", "f2f02908", False),  # xacquire lock add vs xacquire lock sub: two prefixes
    ("ARM", ["ARM"], "10009de5", "14109de5", True),  # ldr r0, [sp, #0x10] vs ldr r1, [sp, #0x14]
    # MIPS and PowerPC: a load of another size differs, another slot doesn't
    ("MIPS", ["MIPS32", "BIG_ENDIAN"], "8fa20054", "87a20054", False),
    ("MIPS", ["MIPS32", "BIG_ENDIAN"], "8fa20054", "8fa30058", True),
    ("PPC", ["32", "BIG_ENDIAN"], "80610010", "a0610010", False),
    ("PPC", ["32", "BIG_ENDIAN"], "80610010", "80810014", True),
])
def test_the_residue_reads_real_instructions_of_each_arch(cs_arch, cs_modes, target, yours, same):
    """`lock add` vs `lock sub`, and `[esp + ecx*4 + 0x18]` vs `ecx*8`, came out as no difference, so the diff said
    "your instructions are the target's" over a real one. The rows here are capstone's own text, as the diff's are."""
    import capstone

    mode = sum(getattr(capstone, f"CS_MODE_{m}") for m in cs_modes)
    md = capstone.Cs(getattr(capstone, f"CS_ARCH_{cs_arch}"), mode)
    t, y = (next(md.disasm(bytes.fromhex(code), 0x1000)) for code in (target, yours))
    rows = [("|", f"{t.mnemonic} {t.op_str}", f"{y.mnemonic} {y.op_str}")]
    assert (scoring.residue(rows) == ([], [])) == same, (rows, scoring.shape(rows[0][1]), scoring.shape(rows[0][2]))


def test_last_diff_gives_every_row_of_the_agents_last_compile_without_compiling(tmp_path):
    net, _ = _game(tmp_path, [("f", 200, "open")])
    a, _ = net.local_agent("a")
    fid = net.store.one("SELECT id FROM functions WHERE name='f'")["id"]
    with pytest.raises(NetworkError, match="compile_and_diff first"):
        net.last_diff(a, "f", "game")
    net.keep_diff(a.id, fid, _rows(200))
    text = net.last_diff(a, "f", "game")
    assert "not shown" not in text and "add eax, 199" in text and "not a new compile" in text
    assert net.store.one("SELECT COUNT(*) AS n FROM attempts")["n"] == 0  # nothing recorded
    b, _ = net.local_agent("b")
    with pytest.raises(NetworkError, match="compile_and_diff first"):
        net.last_diff(b, "f", "game")  # another agent's compile isn't yours


def test_x86_float_operands_by_address():
    assert x86.float_operands("mulss xmm0, dword ptr [_D_00412340]") == [(0x412340, "f")]
    assert x86.float_operands("fld qword ptr [D_00412348]") == [(0x412348, "d")]
    assert x86.float_operands("fmul dword ptr [D_00412340+0x4]") == [(0x412344, "f")]
    assert x86.float_operands("comisd xmm1, qword ptr [D_00400000]") == [(0x400000, "d")]
    assert x86.float_operands("mov eax, dword ptr [D_00412340]") == []  # not a float
    assert x86.float_operands("fstp dword ptr [D_00412340]") == []  # a store, not a constant
    assert x86.float_operands("fild dword ptr [D_00412340]") == []  # an integer
    assert x86.float_operands("mulss xmm0, xmm1") == []


class _Console:
    arch, big_endian = "x86", False

    def __init__(self, data):
        self.data = data

    def data_bytes(self, fp, addr, length):
        return self.data.get((addr, length))


def test_the_diff_shows_the_value_of_the_float_the_target_loads():
    console = _Console({(0x412340, 4): struct.pack("<f", 0.017453292), (0x412348, 8): struct.pack("<d", 0.5)})
    rows = [(" ", "mulss xmm0, [_D_00412340]", "mulss xmm0, [_D_00412340]"),
            ("i", "mulss xmm0, [_D_00412340]", "mulss xmm0, [_kDegToRad]"),
            ("|", "fld qword ptr [D_00412348]", "fld dword ptr [x]"),
            ("i", "movss xmm1, [D_00999999]", "movss xmm1, [y]")]  # not in the copy: left alone
    out = with_constant_values(rows, console, None)
    assert out[0] == rows[0]  # equal rows aren't read
    assert out[1][2] == "mulss xmm0, [_kDegToRad]   ; target's D_00412340 = 0.0174532924f"
    assert out[2][2].endswith("; target's D_00412348 = 0.5")
    assert out[3] == rows[3]
    mips = _Console({})
    mips.arch = "mips"
    assert with_constant_values(rows, mips, None) == rows
