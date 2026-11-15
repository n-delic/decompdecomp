"""Use the pinned native rewrite, then link independently generated C units."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app import permute


def generated():
    tool = toolchains.path(permute.TOOL)
    if not toolchains.installed(permute.TOOL):
        pytest.skip('needs pinned decomp-permuter')
    from decompnet.permuter_gen import __file__ as script
    driver = '''
import sys,json,random
sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2])
from src import ast_util,randomizer
from permuter_gen import private_inline
results=[]
for seed in range(100):
    ast=ast_util.parse_c('inline int kept(int y){return y+7;} int f(unsigned char x){return 123+x*4;}')
    kept,fn=ast.ext
    indices=ast_util.compute_node_indices(fn)
    try:private_inline(randomizer.perm_inline)(fn,ast,indices,randomizer.Region.unbounded(),random.Random(seed))
    except randomizer.RandomizationFailure:continue
    assert kept.decl.storage==[], 'changed a pre-existing function'
    helpers=[n for n in ast.ext if n is not kept and n is not fn]
    assert helpers and all(n.decl.storage==['static'] for n in helpers)
    ast.ext.remove(kept)
    results.append(ast_util.to_c(ast))
    if len(results)==2:break
assert len(results)==2
print(json.dumps(results))
'''
    run = subprocess.run([str(toolchains.venv_bin(tool/'venv', 'python')), '-I', '-c', driver,
                          str(tool), str(Path(script).parent)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_generated_inline_helpers_link_across_units_and_preserve_results(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('needs host C compiler')
    variants = generated()
    main = tmp_path/'main.c'
    main.write_text('int f0(unsigned char);int f1(unsigned char);'
                    'int main(void){int i;for(i=0;i<256;i++)'
                    'if(f0(i)!=123+i*4 || f1(i)!=123+i*4)return 1;return 0;}')
    for external in (False, True):
        objects = []
        for i, source in enumerate(variants):
            source = source.replace('int f(', f'int f{i}(')
            if external:
                source = source.replace('static ', '')
            path = tmp_path/f'{external}-{i}.c'
            obj = path.with_suffix('.o')
            path.write_text(source)
            subprocess.run([cc, '-std=gnu89', '-O0', '-c', str(path), '-o', str(obj)],
                           check=True, capture_output=True, text=True, timeout=20)
            objects.append(str(obj))
        binary = tmp_path/('external' if external else 'private')
        link = subprocess.run([cc, *objects, str(main), '-o', str(binary)],
                              capture_output=True, text=True, timeout=20)
        if external:
            assert link.returncode != 0 and 'inline_fn' in link.stderr, link.stderr
        else:
            assert link.returncode == 0, link.stderr
            subprocess.run([str(binary)], check=True, timeout=10)


@pytest.mark.parametrize('trace', [False, True])
def test_generator_keeps_new_helpers_private_with_or_without_tracing(trace):
    if not toolchains.installed(permute.TOOL):
        pytest.skip('needs pinned decomp-permuter')
    generator = permute.Generator()
    try:
        rows = generator.batch({'header':'', 'source':'int f(unsigned char x,int y){return 123+x*4+y*3;}',
                                'function':'f', 'family':'gcc', 'seed':0, 'count':128, 'trace':trace})
        assert rows is not None
        helpers = []
        for row in rows:
            helpers.extend(re.findall(r'(?m)^([^\n]*\binline\b[^\n]*\binline_fn\w*\s*\([^\n]*)', row))
        assert helpers, 'fixed seed should exercise native helper generation'
        assert all(re.search(r'\bstatic\b', declaration) for declaration in helpers)
    finally:
        generator.close()
