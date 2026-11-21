"""The allocation-priority report: GCC 2.x's own register ranking read back to explain saved-register swaps."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import project, toolchains
from decompnet.app import allocation_priority as ap

# a trimmed `-dl` and `-dg` pair in the compiler's own format: pseudo 83 is a loop counter (set to 0, then plus 1) that
# outranks pseudo 80, a stride computed once per iteration; both are live together, and 88 never overlaps 83
LREG = """
Register 80 used 6 times across 22 insns; dies in 0 places; crosses 3 calls; GR_REGS or none.
Register 83 used 10 times across 26 insns; dies in 0 places; crosses 3 calls; GR_REGS or none.
Register 88 used 31 times across 222 insns; dies in 0 places; crosses 3 calls; GR_REGS or none.
(insn 884 882 886 (set (reg/v:SI 83) (const_int 0)) 159 {movsi_internal2} (nil) (nil))
(insn 952 947 957 (set (reg/v:SI 83) (plus:SI (reg/v:SI 83) (const_int 1))) 3 {addsi3_internal} (nil) (nil))
(insn 892 891 895 (set (reg/v:SI 80) (ashift:SI (reg:SI 226) (const_int 3))) 181 {ashlsi3} (nil) (nil))
(insn 41 38 43 (set (reg/v:SI 88) (symbol_ref:SI ("D_800E1310"))) 159 {movsi_internal2} (nil) (nil))
"""
GREG = """
;; 3 regs to allocate: 83 80 88
;; 80 conflicts: 80 83 2 4
;; 83 conflicts: 80 83 2 4
;; 88 conflicts: 88 2 4

;; Register dispositions:
80 in 19  83 in 18  88 in 19

;; Hard regs used:  2 18 19
"""


def test_the_dumps_become_a_table_in_allocation_order():
    table = ap.parse(LREG, GREG)
    assert [p.number for p in table] == [83, 80, 88]
    counter, stride, address = table
    assert counter.role == "loop counter" and counter.uses == 10 and counter.length == 26
    assert counter.priority == pytest.approx(3 * 10 / 26)  # floor_log2(10) * 10 / 26
    assert stride.priority == pytest.approx(2 * 6 / 22)
    assert address.role == "address of D_800E1310"
    assert (counter.hard, stride.hard) == (18, 19) and counter.conflicts == {80, 83, 2, 4}
    assert ap.parse("", "") == []


def test_a_swap_is_explained_by_the_pseudos_that_compete_for_the_registers():
    table = ap.parse(LREG, GREG)
    rows = [["r", "move $s3, $zero", "move $s2, $zero"], ["r", "sll $s2, $v0, 3", "sll $s3, $v0, 3"],
            [" ", "jal func_8002E98C", "jal func_8002E98C"]]
    assert ap.swaps(rows) == {("$s2", "$s3"): 1, ("$s3", "$s2"): 1}
    (note,) = ap.advice(table, rows)  # the two mirrored rows are one swap
    assert "Register swap $s2 <-> $s3 (2 instructions)" in note
    assert "loop counter (pseudo 83" in note and "priority 1.15" in note
    assert "address of D_800E1310" not in note  # 88 holds $s3 too, but is never live with the counter
    assert "stride" not in note and "pseudo 80" in note
    assert "only the byte judge accepts a match" in note


def test_no_advice_without_competing_saved_registers():
    table = ap.parse(LREG, GREG)
    assert ap.advice(table, [[" ", "nop", "nop"]]) == []
    assert ap.advice([], [["r", "move $s3, $zero", "move $s2, $zero"]]) == []
    # caller-saved swaps follow instruction order, not this ranking
    assert ap.advice(table, [["r", "addu $v0, $v0, $a0", "addu $v1, $v0, $a0"]]) == []
    # two saved registers whose pseudos never overlap are no rivalry
    apart = ap.parse(LREG, GREG.replace(";; 80 conflicts: 80 83 2 4", ";; 80 conflicts: 80 2 4").replace(
        ";; 83 conflicts: 80 83 2 4", ";; 83 conflicts: 83 2 4"))
    assert ap.advice(apart, [["r", "move $s3, $zero", "move $s2, $zero"]]) == []


def test_only_gcc_two_drivers_print_the_dumps():
    assert ap.enabled(["/x/toolchains/gcc-2.7.2-kmc/gcc", "-O2"])
    assert ap.enabled(["/x/toolchains/gcc-egcs-2.91.66/gcc"])
    assert ap.enabled(["C:\\x\\toolchains\\gcc-2.8.1-pm\\gcc.exe"])
    assert not ap.enabled(["/x/toolchains/ido-7.1/cc"]) and not ap.enabled([]) and not ap.enabled(["/usr/bin/gcc"])


def test_hints_only_for_mips_register_swaps(monkeypatch):
    called = []
    monkeypatch.setattr(ap, "dump", lambda *a: called.append(a) or ap.parse(LREG, GREG))
    net = SimpleNamespace(_invocation=lambda proj, fn: (None, SimpleNamespace(cmd=["x"])))
    fn = {"id": 1}
    swapped = [["r", "move $s3, $zero", "move $s2, $zero"]]
    assert ap.hints(net, {"arch": "x86"}, fn, "", "int f(void){}", swapped) == []
    assert ap.hints(net, {"arch": "mips"}, fn, "", "int f(void){}", [[" ", "nop", "nop"]]) == []
    assert not called
    assert "Register swap" in ap.hints(net, {"arch": "mips"}, fn, "", "int f2(void){}", swapped)[0]
    ap.hints(net, {"arch": "mips"}, fn, "", "int f2(void){}", swapped)
    assert len(called) == 1  # the same candidate compiles once


@pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None, reason="needs the KMC GCC (CI has none)")
def test_the_real_compiler_prints_a_table_for_a_function_with_loops():
    cfg = project.compiler_config(Path("."), {"compiler": {"toolchain": "gcc-2.7.2-kmc", "timeout": 60,
                                                           "flags": ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]}})
    source = """
extern int f(void *, int);
extern char buf[], out[];
void g(void) {
    int i, n = 0;
    for (i = 0; i < 9; i++) { out[i] = f(&buf, 1); n += f(&buf, 2); }
    for (i = 0; i < 3; i++) { out[i] = f(&buf, 3); }
}
"""
    table = ap.dump(cfg, "", source)
    assert table, "no allocation table: -dl -dg dumps were not written"
    assert any(p.role == "loop counter" for p in table)
    assert all(p.order is not None and p.length > 0 for p in table)
    assert any(p.hard in range(16, 24) for p in table)  # something lives across the calls in a saved register
    assert ap.table_text(table).count("\n") >= 0
