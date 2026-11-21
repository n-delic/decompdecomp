"""The switch arm store family (decomp-repair) is one of the local repairs, and its effect on GCC 2.7's scheduling."""
import subprocess

import pytest

from decompnet import toolchains
from decompnet.app import local_experiments, repair_ranking
from decompnet.app.repair_generation import call
from decomprepair import loop_counter_roles_repairs, switch_arm_store_repairs

# m2c's form: the arms gather one local and one store follows the switch
SOURCE = '''extern const unsigned *volatile G;
extern int H, K;
extern int f(void *, int, int);
extern int sh(void *, int);
extern unsigned char A[], B[];
extern void tbl(void);

void t(int m) {
    const unsigned *v;
    int i;
    switch (m) {
    case 5:
    case 6:
        v = (const unsigned *)((const unsigned char *)tbl + 0x1C4);
        break;
    case 2:
        v = (const unsigned *)((const unsigned char *)tbl + 0x198);
        break;
    default:
        v = (const unsigned *)((const unsigned char *)tbl + 0x180);
        break;
    }
    G = v;
    H = (int)*(K + G);
    K = f(&B, 1, 8);
    K = f(&B, 4, 0x20);
    i = 0;
    do {
        int j = (i & 0xFFFF) * 8;
        sh(j + (unsigned char *)&A, 0x1E);
        *(int *)((unsigned char *)&A + j) = f(&B, 4, 0xD);
        i += 1;
    } while ((unsigned)(i & 0xFFFF) < 3U);
}
'''


def test_the_family_is_registered_for_local_repairs():
    assert local_experiments.switch_arm_store_repairs is switch_arm_store_repairs
    assert switch_arm_store_repairs.FAMILY in local_experiments.FAMILIES
    assert "'switch_arm_store'" in open(repair_ranking.__file__, encoding='utf-8').read()


def test_its_variants_come_through_the_shared_generator():
    pairs = call(switch_arm_store_repairs.variants, SOURCE, 't')
    assert [family for family, _ in pairs] == ['switch_arm_store']
    text = pairs[0][1]
    assert text.count('G = (const unsigned *)((const unsigned char *)tbl + 0x') == 3 and 'G = v;' not in text


def test_counter_roles_reach_goto_loops_and_guarded_counters():
    # (decomp-repair 0.5.170) a goto loop counts as a counter loop, and a counter read after its last loop can take loops
    source = '''typedef int s32;
extern unsigned char A[], B[];
s32 reader(void *, s32);

void g(void) {
    s32 a;
    s32 b;
    a = 0;
    do {
        A[a] = reader(A, 1);
        a += 1;
    } while (a < 3);
    b = 0;
loop:
    B[b] = reader(B, 2);
    b += 1;
    if (b < 3) {
        goto loop;
    }
    A[0] = b;
}
'''
    pairs = call(loop_counter_roles_repairs.variants, source, 'g')
    assert any(family == 'loop_counter_reassign' and '    b = 0;\n    do {\n        A[b] = reader(A, 1);' in text
               for family, text in pairs)  # the first loop counts with `b`; `A[0] = b` still reads the goto loop's last value
    assert all('A[0] = b;' in text and 'B[b] = reader(B, 2);' in text for _, text in pairs)


@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None, reason='needs the KMC GCC (CI has none)')
def test_a_store_in_each_arm_is_scheduled_before_the_next_calls_argument_setup(tmp_path):
    """The reason for the family: GCC 2.x cross-jumps the identical per-arm stores after the first scheduling pass, so
    the store is in its own scheduling block, while the merged form shares a block with the next call's argument setup,
    which the first pass moves above the store (seen on a GCC 2.7 near miss as 8 differing words)."""
    root = toolchains.path('gcc-2.7.2-kmc')
    arm = call(switch_arm_store_repairs.variants, SOURCE, 't')[0][1]

    def order(source):
        (tmp_path / 't.c').write_text(source)
        run = subprocess.run([str(root / 'gcc'), f'-B{root}/', '-G0', '-mips3', '-mgp32', '-mfp32', '-O2', '-S', 't.c', '-o', 't.s'],
                             cwd=tmp_path, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        lines = [line.strip() for line in (tmp_path / 't.s').read_text().splitlines()]
        stores = [n for n, line in enumerate(lines) if line.startswith('sw') and line.endswith(',G')]
        loads = [n for n, line in enumerate(lines) if line.startswith('la') and line.endswith(',B')]
        return stores, loads
    merged_stores, merged_loads = order(SOURCE)
    arm_stores, arm_loads = order(arm)
    assert len(merged_stores) == len(arm_stores) == 1  # the arms' stores are merged back into one
    assert merged_loads[0] < merged_stores[0]  # merged: the argument setup is hoisted above the store
    assert arm_stores[0] < arm_loads[0]  # per-arm: the store stays first
