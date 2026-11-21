"""The loop address hoist family (decomp-repair) is one of the local repairs the agents and the deep search try."""
from decompnet.app import local_experiments
from decompnet.app.repair_generation import call
from decomprepair import loop_address_hoist_repairs

SOURCE = '''typedef int s32;
extern s32 D_800E1310;
s32 reader(void *, s32, s32);

void f(void) {
    s32 i;

    i = 0;
    do {
        reader(&D_800E1310, 1, 1);
        reader(&D_800E1310, 1, 4);
        reader(&D_800E1310, 1, 8);
        i += 1;
    } while (i < 11);
}
'''


def test_the_family_is_registered_for_local_repairs():
    assert 'loop_address_hoist' in local_experiments.FAMILIES
    assert local_experiments.loop_address_hoist_repairs is loop_address_hoist_repairs


def test_its_variants_come_through_the_shared_generator():
    # A GCC 2.7 near miss went from 465 to 405 with the local standing for the address in the outer loop only.
    pairs = call(loop_address_hoist_repairs.variants, SOURCE, 'f')
    assert pairs and {family for family, _ in pairs} == {'loop_address_hoist'}
    assert any('hoist_D_800E1310 = &D_800E1310;\n    do {' in text and 'reader(hoist_D_800E1310, 1, 8)' in text
               for _, text in pairs)
