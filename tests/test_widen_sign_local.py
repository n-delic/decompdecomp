"""decomp-repair 0.5.203 (`kmc_type_repairs`, family `kmc_widen_sign_local`): a 16-bit local widened when the target
reads its own saved register where KMC GCC's shared sign-extension copy put the argument register. The search reaches
it through the focused `kmc_type_repairs.variants` call the Deep Permuter already makes for gcc-2.7.2-kmc."""
from decomprepair import kmc_type_repairs

SOURCE = """void f(void *a) {
    s16 mode = 0;
    switch (kind) { case 1: mode = 4; break; case 2: mode = 0x16; break; }
    first = mode - first;
    use(mode, first);
}"""
ROWS = [(" ", "move $a0, $s0", "move $a0, $s0"), ("r", "subu $a1, $s0, $a1", "subu $a1, $a0, $a1")]


def test_the_pinned_library_widens_a_sign_local_for_a_saved_against_argument_register_row():
    [(family, text)] = kmc_type_repairs.variants(SOURCE, "f", "", ROWS)
    assert family == "kmc_widen_sign_local" and "s32 mode = 0;" in text
    assert not kmc_type_repairs.variants(SOURCE, "f", "", [("r", "subu $a1, $s0, $a1", "subu $a1, $s1, $a1")])
