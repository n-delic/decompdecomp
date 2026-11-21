"""A mask that must be the first operand of `and`: KMC GCC's cse swaps a known constant second, so the search hides it.

decomp-repair 0.5.196 (`kmc_operand_repairs`, family `mask_operand_order`) proposes `((x & mask) | mask)` when the diff
has an `and` with reversed source registers. These tests check the app's side of it: the pinned library has the family,
the focused path the Deep Permuter uses reaches it, and (with the real compiler) the rewrite is what flips the order."""
import io
import re
from pathlib import Path

import pytest

from decompnet import compiler, project, toolchains
from decomprepair import kmc_operand_repairs

# A flag-clearing function in the search's own dialect: the mask lives in a local, applied with `&=` to a loaded word.
SOURCE = """typedef unsigned int u32;
extern void *spawn(int, int, int);
void *f(void *arg0) {
    void *obj = spawn(4, 0, 0);
    if (obj) {
        u32 *flags = (u32 *)((char *)arg0 + 0x58);
        u32 old = *flags;
        u32 updated = ~0x1c0U;
        updated &= old;
        *flags = updated;
    }
    return obj;
}
"""
# target `and $v0,$v0,$v1` (mask first) against the candidate's `and $v0,$v1,$v0`
ROWS = [(" ", "lw $v1, 0x58($s0)", "lw $v1, 0x58($s0)"), ("r", "and $v0, $v0, $v1", "and $v0, $v1, $v0")]


def test_the_pinned_library_proposes_the_mask_rewrite_for_a_swapped_and():
    proposals = kmc_operand_repairs.variants(SOURCE, "f", "", ROWS)
    [rewrite] = [text for family, text in proposals if family == "mask_operand_order"]
    assert "u32 updated = ((old & ~0x1c0U) | ~0x1c0U);" in rewrite
    # with no swapped `and` in the diff nothing is proposed: the search does not spend compiles on it
    assert not kmc_operand_repairs.variants(SOURCE, "f", "", [(" ", "and $v0, $v1, $v0", "and $v0, $v1, $v0")])


def _and_order(source):
    cfg = project.compiler_config(Path("."), {"compiler": {"toolchain": "gcc-2.7.2-kmc", "timeout": 60,
                                                           "flags": ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]}})
    result = compiler.compile_source(cfg, "", source)
    assert result.ok, result.log
    import capstone
    from elftools.elf.elffile import ELFFile
    text = ELFFile(io.BytesIO(result.obj)).get_section_by_name(".text").data()
    cs = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 | capstone.CS_MODE_BIG_ENDIAN)
    lines = [f"{i.mnemonic} {i.op_str}" for i in cs.disasm(text, 0)]
    mask = next(m[1] for line in lines if (m := re.match(r"addiu (\$\w+), \$zero, (?:-449|-0x1c1)$", line)))
    and_line = next(line for line in lines if line.startswith("and "))
    _, _, first, _ = re.split(r"[ ,]+", and_line)
    return "mask first" if first == mask else "value first"


@pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None, reason="needs the KMC GCC (CI has none)")
def test_the_real_compiler_puts_the_mask_first_only_after_the_rewrite():
    assert _and_order(SOURCE) == "value first"
    [rewrite] = [text for family, text in kmc_operand_repairs.variants(SOURCE, "f", "", ROWS)
                 if family == "mask_operand_order"]
    assert _and_order(rewrite) == "mask first"
