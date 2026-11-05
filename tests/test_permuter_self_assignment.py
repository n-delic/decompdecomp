"""Exclude native self-read/cancellation insertion before it enters either walk."""
import json
import subprocess
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app import permute
from decompnet.permuter_gen import supported_methods


def test_filter_preserves_other_methods_and_weights():
    def perm_add_self_assignment():
        raise AssertionError('must not be selected')

    def useful_rewrite():
        return 'kept'

    assert supported_methods([(perm_add_self_assignment, 2), (useful_rewrite, 3)]) == [(useful_rewrite, 3)]
    assert useful_rewrite() == 'kept'


@pytest.mark.parametrize('trace', [False, True])
def test_real_generator_filters_before_selection_in_both_protocol_modes(trace):
    if not toolchains.installed(permute.TOOL):
        pytest.skip('needs pinned decomp-permuter')
    from decompnet.permuter_gen import __file__ as script
    tool = toolchains.path(permute.TOOL)
    driver = '''
import sys
sys.path.insert(0,sys.argv[1])
import permuter_gen
original=permuter_gen.supported_methods
calls=0
def checked(methods):
    global calls
    assert any(m.__name__=='perm_add_self_assignment' for m,w in methods)
    result=original(methods)
    assert result and all(m.__name__!='perm_add_self_assignment' for m,w in result)
    calls+=1
    return result
permuter_gen.supported_methods=checked
sys.argv=['permuter_gen',sys.argv[2]]
assert permuter_gen.main()==0
assert calls>0
'''
    job = dict(header='', source='int f(int x,int y){int n;n=x*3+y;return n+4;}',
               function='f', family='gcc', seed=17, count=32, trace=trace)
    run = subprocess.run([str(toolchains.venv_bin(tool/'venv', 'python')), '-I', '-c', driver,
                          str(Path(script).parent), str(tool)], input=json.dumps(job)+'\n',
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    rows = [json.loads(line) for line in run.stdout.splitlines()]
    assert rows[-1] == {'done': True}
    variants = [r for r in rows if 'variant' in r]
    assert variants and not any('error' in r for r in rows)
    assert all('perm_add_self_assignment' not in r.get('lineage', []) for r in variants)
