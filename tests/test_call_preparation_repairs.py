import shutil
import subprocess

import pytest

from decompnet.app import draft_repairs, permuter_focus, repair_cleanup, repair_ranking
from decomprepair import call_preparation_repairs as repairs

HEADER = 'typedef unsigned int u32; typedef struct { int words[6]; } Packet; void send(int,int,Packet);'
SOURCE = 'void f(int a,int b,Packet packet){send(a,b,packet);}'


def test_dispatch_keeps_callee_aggregate_abi_and_uses_private_pointer_helper():
    choices = repairs.dispatch(SOURCE, 'f', HEADER, 'mips')
    assert len(choices) == 1
    candidate = choices[0][1]
    assert 'static __inline__ void __repair_aggregate_call_f_send(int arg0, int arg1, Packet *arg2)' in candidate
    assert 'send(arg0, arg1, *arg2)' in candidate and '&(packet)' in candidate
    assert repairs.dispatch(candidate, 'f', HEADER, 'mips') == []
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, header=HEADER, limit=None)
    assert 'aggregate_pointer_dispatch' in labels.values()


@pytest.mark.parametrize('header,source', [
    (HEADER, SOURCE.replace('send(a,b,packet)', 'send(a++,a,packet)')),
    (HEADER, SOURCE.replace('packet);', 'make_packet());')),
    (HEADER.replace('void send(int,int,Packet);', 'void send(int,...);'), SOURCE),
    (HEADER.replace('words[6]', 'words:6'), SOURCE),
    (HEADER.replace('int words', 'volatile int words'), SOURCE),
    (HEADER+'\n#define packet global_packet', SOURCE),
])
def test_unknown_effects_macros_and_interfaces_do_not_generate_helpers(header, source):
    assert repairs.dispatch(source, 'f', header, 'mips') == []


def test_bundles_keep_assignment_conversions_and_try_combinations():
    source = 'void send(int,int); void f(int x,int y){ int a; int b; a=x; b=y; send(a,b); }'
    choices = repairs.bundles(source, 'f', arch='mips')
    assert len(choices) == 3
    assert any('(a = (int)(x))' in c and '(b = (int)(y))' in c for _,c in choices)
    for unsafe in [source.replace('b=y', 'b=a'), source.replace('send(a,b)', 'send(a,a+b)'),
                   source.replace('b=y','b=*p'), source.replace('int a;', 'volatile int a;')]:
        assert repairs.bundles(unsafe, 'f', arch='mips') == []


def test_cleanup_removes_only_a_complete_scalar_assignment_wrapper():
    source = 'int f(int a){ int delta; do { delta = a & 1; } while(0); return delta; }'
    choices = repair_cleanup.variants(source, 'f', arch='mips')
    assert len(choices) == 1 and 'do {' not in choices[0][1]
    for inner in ['int delta; delta=a;', 'delta=a; break;', 'delta=a; continue;', 'delta=READ(a);']:
        assert not repair_cleanup.variants(source.replace('delta = a & 1;', inner), 'f', arch='mips')
    assert not repair_cleanup.variants(source, 'f', '#define delta hidden', 'mips')


def test_order_only_focus_requires_identical_complete_instruction_multisets():
    rows = [('o','move $a0, $s2',''), (' ','lw $v0, ($v0)','lw $v0, ($v0)'), ('o','','move $a0, $s2')]
    assert permuter_focus.classify(rows) == 'scheduling'
    families = ['local_width','aggregate_pointer_dispatch','revisit_one_shot_scope']
    assert repair_ranking.order(families, rows)[0] == 'revisit_one_shot_scope'
    assert not permuter_focus.scheduling_only(rows[:-1])
    assert not permuter_focus.scheduling_only([('r','move $v0, $a0','move $v0, $a1')])
    assert not permuter_focus.scheduling_only([('o',None,'x')])


def test_host_execution_preserves_aggregate_values_and_rejects_noninlined_helper(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('needs host C compiler')
    from decompnet.inline_helpers import check
    candidate = repairs.dispatch(SOURCE, 'f', HEADER, 'mips')[0][1]
    main = '\nint total; void send(int a,int b,Packet p){ total=a+b+p.words[5]; } int main(void){ Packet p={{0,0,0,0,0,7}}; f(2,3,p); return total!=12; }'
    path = tmp_path/'control.c'
    path.write_text(HEADER+'\n'+candidate+main)
    for optimization in ['-O0','-O2']:
        obj = tmp_path/(optimization+'.o')
        subprocess.run([cc, '-std=gnu89', optimization, '-c', str(path), '-o', str(obj)], check=True, capture_output=True)
        # macOS host objects are Mach-O: the runtime guard deliberately fails closed there.
        verdict = check(candidate, obj.read_bytes())
        if obj.read_bytes().startswith(b'\x7fELF'):
            assert bool(verdict) == (optimization=='-O0')
        else:
            assert verdict
        binary = tmp_path/'run'
        subprocess.run([cc, str(obj), '-o', str(binary)], check=True, capture_output=True)
        subprocess.run([str(binary)], check=True)


def test_direct_record_fields_can_be_bundled_but_pointer_aliases_cannot():
    header = 'typedef struct { int start; int time; } Record; void send(int,int);'
    source = 'void f(int a,int b){ Record r; r.start=a; r.time=b; send(a,b); }'
    choices = repairs.field_bundles(source, 'f', header, 'mips')
    assert len(choices) == 3
    assert any('r.start=a, a' in c and 'r.time=b, b' in c for _,c in choices)
    assert not repairs.field_bundles(source.replace('Record r;', 'Record *r;').replace('r.', 'r->'), 'f', header, 'mips')
    assert not repairs.field_bundles(source.replace('send(a,b)', 'send(a,r.start)'), 'f', header, 'mips')
    assert not repairs.field_bundles(source.replace('r.time=b;', 'r.start=b;'), 'f', header, 'mips')


def test_cleanup_and_preparation_are_registered_as_complementary_families():
    from decompnet.app.repair_selection import order
    families = ['local_width','revisit_one_shot_scope','call_preparation_bundle']
    assert order(families, ['aggregate_pointer_dispatch'])[:2] == ['call_preparation_bundle','revisit_one_shot_scope']


def test_cleanup_allows_unrelated_volatile_header_declarations():
    source = 'int f(int a){ int delta; do { delta=a & 1; } while(0); return delta; }'
    assert repair_cleanup.variants(source, 'f', 'extern volatile int device;', 'mips')
    assert not repair_cleanup.variants(source.replace('int delta;', 'volatile int delta;'), 'f', arch='mips')


def test_unknown_architecture_is_not_assigned_a_guessed_scalar_contract():
    assert not repairs.dispatch(SOURCE, 'f', HEADER, 'unknown')
    assert not repairs.bundles('void f(int x){int a;a=x;send(a);}', 'f', arch='unknown')


def test_pinned_kmc_inlines_generated_helper_and_guard_rejects_disabled_inlining(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned KMC GCC 2.7.2')
    candidate = repairs.dispatch(SOURCE, 'f', HEADER, 'mips')[0][1]
    profile = {'toolchain':'gcc-2.7.2-kmc','flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}
    cfg = compiler_config(tmp_path, {'compiler':profile})
    result = compile_source(cfg, HEADER, candidate)
    assert result.ok, result.log
    profile['flags'].append('-fno-inline')
    result = compile_source(compiler_config(tmp_path, {'compiler':profile}), HEADER, candidate)
    assert not result.ok and 'did not fully inline' in result.log


def test_imported_search_does_not_build_a_helper_it_cannot_place():
    from types import SimpleNamespace

    from decompnet.app.permute import Permuter
    from tests.test_permute import AGENT, FakeNet
    net = FakeNet()
    net.forks = SimpleNamespace(attempt=lambda *a,**kw:pytest.fail('unsupported helper should not start a build'))
    permuter = Permuter(net)
    source = repairs.dispatch(SOURCE, 'f', HEADER, 'mips')[0][1]
    assert permuter._score_work(AGENT, net.project_row('game'), net.function(), source, 40, 'fork') is None
    assert permuter._trial_outcome == 'unsupported helper placement'
