import json
import subprocess
from pathlib import Path

import pytest

from decompnet import limits, toolchains
from decompnet.app import permute
from decompnet.permuter_gen import __file__ as generator

DRIVER = """
import sys
sys.path.insert(0, sys.argv[1])
import permuter_gen

def mutate(fn, *args):
    from perm_pycparser import c_parser
    replacement = c_parser.CParser().parse(
        'unsigned f(unsigned x) { return (unsigned short)(x | 0xe4000000U); }')
    fn.body.block_items[0].expr = replacement.ext[0].body.block_items[0].expr

mutate.__name__ = 'perm_cast_simple'
permuter_gen.supported_methods = lambda methods: [(mutate, 1)]
sys.argv = ['permuter_gen', sys.argv[2]]
raise SystemExit(permuter_gen.main())
"""


@pytest.mark.parametrize('trace', [False, True])
def test_native_generator_discards_candidate_with_lost_opcode(trace):
    if not toolchains.installed(permute.TOOL):
        pytest.skip('needs pinned decomp-permuter')
    tool = toolchains.path(permute.TOOL)
    job = dict(header='', source='unsigned f(unsigned x) { return x | 0xe4000000U; }',
               function='f', family='gcc', seed=7, count=4, trace=trace)
    command = [str(toolchains.venv_bin(tool / 'venv', 'python')), '-I', '-c', DRIVER,
               str(Path(generator).parent), str(tool)]
    result = subprocess.run(limits.capped(command), input=json.dumps(job) + '\n',
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert [json.loads(line) for line in result.stdout.splitlines()] == [{'done': True}]
