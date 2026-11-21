"""The loop counter roles family (decomp-repair) is one of the local repairs the agents and the deep search try."""
from decompnet.app import local_experiments, repair_ranking
from decompnet.app.repair_generation import call
from decomprepair import loop_counter_roles_repairs

# two do-while loops that share one counter, as m2c writes them for a binary that kept it in one saved register
SOURCE = '''typedef int s32;
extern unsigned char A[], B[];
s32 reader(void *, s32);

void f(void) {
    s32 i;

    i = 0;
    do {
        A[i] = reader(A, 1);
        i += 1;
    } while (i < 4);
    i = 0;
    do {
        B[i] = reader(B, 2);
        i += 1;
    } while (i < 5);
}
'''
LABELS = ('loop_counter_fresh', 'loop_counter_reassign', 'loop_counter_merge', 'loop_counter_swap')


def test_the_family_is_registered_for_local_repairs():
    assert local_experiments.loop_counter_roles_repairs is loop_counter_roles_repairs
    assert all(label in local_experiments.FAMILIES for label in LABELS)
    assert local_experiments.COUNTER_ROLES == LABELS
    text = open(repair_ranking.__file__, encoding='utf-8').read()
    assert all(f"'{label}'" in text for label in LABELS)  # ranked with the other register-oriented families


def test_its_variants_come_through_the_shared_generator():
    # On one GCC 2.7 near miss its 24 variants all compiled and moved the differing words between 22 and 151;
    # it has matched nothing yet.
    pairs = call(loop_counter_roles_repairs.variants, SOURCE, 'f')
    assert pairs and {family for family, _ in pairs} <= set(LABELS)
    assert any(family == 'loop_counter_fresh' and 'i_b = 0;\n    do {\n        B[i_b]' in text for family, text in pairs)
