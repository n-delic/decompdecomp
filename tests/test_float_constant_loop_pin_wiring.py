"""float_constant_loop_pin (decomprepair 0.5.624): KMC GCC 2.7.2's loop.c hoists a float constant out of a loop into a
callee-saved FP register (a bigger frame, every later FP register shifted); one shared local assigned in place at each
compare is never moved (scan_loop: a named local's pseudo is movable only inside one basic block), so the zero is
loaded where it is used, as the game does. The family is reached from the drafts and the Deep Permuter's local lane."""
import io
from pathlib import Path

import pytest

from decompnet import compiler, project, toolchains
from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
typedef float f32;
extern int hit(f32 *, int);
int sweep(int a, int b, f32 best)
{
    f32 out;
    int i;
    int found = -1;
    for (i = 0; i < a; i++) {
        if (hit(&out, i) && out >= 0.0f && out < best) {
            best = out;
            found = i;
        }
    }
    for (i = 0; i < b; i++) {
        if (hit(&out, i + 100) && out >= 0.0f && out < best) {
            best = out;
            found = i;
        }
    }
    return found;
}
"""


def test_the_drafts_offer_the_pin():
    labels = {}
    draft_repairs.variants(SOURCE, 'sweep', 'mips', labels=labels, limit=None)
    assert 'float_constant_loop_pin' in set(labels.values())
    assert 'float_constant_loop_pin' in draft_repairs.AGENT_FAMILIES


def test_the_pin_runs_in_the_bounded_local_lane(monkeypatch):
    from decomprepair import float_constant_loop_pin
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)  # the stub lives in this process, not the proposal child's
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* constant kept in its loop */'
    monkeypatch.setattr(float_constant_loop_pin, 'variants',
                        lambda *args: [('float_constant_loop_pin', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert tried == [child]


def _assembly(source):
    import capstone
    from elftools.elf.elffile import ELFFile
    cfg = project.compiler_config(Path('.'), {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'timeout': 60,
                                                           'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']}})
    result = compiler.compile_source(cfg, '', source)
    assert result.ok, result.log
    text = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text')
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 | capstone.CS_MODE_BIG_ENDIAN)
    return [(i.mnemonic + ' ' + i.op_str).strip() for i in md.disasm(text.data(), 0)]


@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None, reason='needs the KMC GCC (CI has none)')
def test_the_pinned_zero_stays_in_the_loop_and_the_saved_register_goes():
    from decomprepair import float_constant_loop_pin

    plain = _assembly(SOURCE)
    # the plain source: loop.c hoists the zero into a callee-saved FP register ($f22 here) before the loops
    hoisted = [line for line in plain if line.startswith('mtc1 $zero') and line.split(', ')[-1] != '$f0']
    assert hoisted and not any('$f0' == line.split(', ')[-1] for line in plain if line.startswith('mtc1 $zero'))
    assert any(line.startswith('sdc1 $f22') for line in plain)

    family, pinned = float_constant_loop_pin.variants(SOURCE, 'sweep')[0]
    assert family == 'float_constant_loop_pin'
    lines = _assembly(pinned)
    zeros = [line for line in lines if line.startswith('mtc1 $zero')]
    assert zeros and all(line.endswith('$f0') for line in zeros)  # loaded where it is compared, in both loops
    assert len(zeros) == 2
    assert not any(line.startswith('sdc1 $f22') for line in lines)  # no extra saved FP register for the zero
