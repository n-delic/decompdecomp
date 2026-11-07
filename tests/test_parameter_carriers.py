import shutil
import subprocess

import pytest

from decompnet import limits
from decompnet.app import draft_repairs, parameter_carriers

SOURCE = '''void f(int input, int flag, int *out) {
 int product; int prev; int next;
 product = input * 3;
 input = product + 7;
 prev = input - 2;
 next = input + 2;
 if (flag) { out[0]=input; out[1]=prev; } else { out[0]=next; out[1]=input; }
}'''


def generated(source=SOURCE, header='', arch='mips'):
    return [s for _,s in parameter_carriers.variants(source,'f',header,arch)]


@pytest.mark.parametrize('arch',['mips','ppc','arm','thumb','x86'])
def test_coordinated_cycle_keeps_entry_parameter_until_last_use(arch):
    proposals=generated(arch=arch)
    assert proposals and len(proposals)<=parameter_carriers.LIMIT
    assert all('void f(int input, int flag, int *out)' in s and 'product = input * 3;' in s for s in proposals)
    assert any('prev = product + 7;' in s and 'next = prev - 2;' in s and 'input = prev + 2;' in s for s in proposals)


@pytest.mark.parametrize('change',[
    lambda s:s.replace('int input','short input'),
    lambda s:s.replace('prev = input - 2;','prev = input - 2; use(&input); use(&prev); use(&next);'),
    lambda s:s.replace('product = input * 3;','product = input * 3; { unsigned char input; use(input); }'),
    lambda s:s.replace('product = input * 3;','product = input * 3; { Alias input; use(input); }'),
    lambda s:s.replace('if (flag)','while (flag)'),
    lambda s:s.replace('prev = input - 2;','goto done; prev = input - 2; done:;'),
])
def test_unsupported_width_escape_shadow_and_cross_phase_control_are_rejected(change):
    assert not generated(change(SOURCE),header='typedef int Alias;')


def test_hidden_control_or_capture_is_not_rewritten():
    assert not generated(SOURCE.replace('product = input * 3;','HIDDEN(); product = input * 3;'),
                         '#define HIDDEN() longjmp(env,1)')
    assert not generated(SOURCE,'#define INPUT() input')
    assert not generated(arch='unknown')
    assert parameter_carriers.FAMILY in draft_repairs.AGENT_FAMILIES


def test_dead_parameter_can_hold_one_later_local():
    source='int f(int input) { int result; result=input*3; use(result); return result; }'
    # Entry input remains live in the result's RHS, so that phase cannot be renamed.
    assert not generated(source)
    source='int f(int input) { int product; int result; product=input*3; result=product+7; return result; }'
    assert any('input=product+7; return input;' in s for s in generated(source))


def test_every_proposal_preserves_both_paths_on_independent_host_control(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('needs a host C compiler')
    proposals=generated()
    definitions=[SOURCE.replace('void f(', 'void reference(')]
    definitions += [s.replace('void f(',f'void candidate{i}(') for i,s in enumerate(proposals)]
    checks=''.join(f'candidate{i}(x,flag,b); if(a[0]!=b[0] || a[1]!=b[1]) return 1;' for i in range(len(proposals)))
    text='\n'.join(definitions)+'\nint main(void) { int x,flag,a[2],b[2]; for(x=-100;x<=100;x++) for(flag=0;flag<2;flag++) { reference(x,flag,a); '+checks+' } return 0; }'
    path=tmp_path/'control.c';path.write_text(text);exe=tmp_path/'control'
    built=subprocess.run(limits.capped([cc,str(path),'-o',str(exe)],memory=1<<30),capture_output=True,text=True,timeout=30)
    assert built.returncode==0,built.stderr
    assert subprocess.run([str(exe)],timeout=10).returncode==0
