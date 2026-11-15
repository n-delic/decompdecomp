"""Exercise the actual pinned expander, including its assignment-removal path."""
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app import permute

TOOL = toolchains.path(permute.TOOL)
pytestmark = pytest.mark.skipif(toolchains.installed(permute.TOOL) is None, reason='needs decomp-permuter')

SCRIPT = '''
import json,sys,random
sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2])
from src import ast_util,randomizer
from perm_pycparser import c_ast
from permuter_capture import stable_reads,protect_expand
source,blocked=json.loads(sys.stdin.read())
ast=ast_util.parse_c(source);fn=next(n for n in ast.ext if isinstance(n,c_ast.FuncDef));indices=ast_util.compute_node_indices(fn)
reads=randomizer.compute_read_locations(fn,indices);writes=randomizer.compute_write_locations(fn,indices)
filtered=stable_reads(fn,indices,writes,reads,blocked,[n for n in ast.ext if isinstance(n,c_ast.Typedef)])
print(json.dumps({k:len(v) for k,v in filtered.items()}))
'''


def allowed(source, blocked=()):
    from decompnet.permuter_gen import __file__ as script
    run = subprocess.run([str(toolchains.venv_bin(TOOL/'venv', 'python')), '-I', '-c', SCRIPT, str(TOOL), str(Path(script).parent)],
                         input=json.dumps([source, blocked]), text=True, capture_output=True, timeout=20)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.parametrize('middle,expected', [
    ('', 2), ('i=3;', 1), ('i++;', 1), ('i+=3;', 1),
    ('if(i) i=3;', 1), ('i=3; i=0;', 1),
])
def test_later_operand_writes_do_not_expand_captured_pointer(middle, expected):
    source = 'void f(char *base,int i){char *p;p=base+i;*p=1;'+middle+'*p=2;}'
    assert allowed(source).get('p', 0) == expected


@pytest.mark.parametrize('source', [
    'void f(char*base,int i){char*p;p=base+i;i=3;*p=2;}',
    'void f(char*base,int i){char*p;p=base+i;*p=i++;}',
    'void f(char*base,int i){char*p;p=base+i;while(i--)*p=1;}',
    'void f(char*base,int i){char*p;while(i){p=base+i;i--;*p=1;}}',
    'void f(char*base,int i){char*p;p=base+i;goto use;use:*p=1;}',
    'void f(char*base,int i){char*p;int*escape=&i;p=base+i;*p=1;}',
    'typedef volatile int V;void f(char*base,V i){char*p;p=base+i;*p=1;}',
    'void f(char*base){typedef volatile int V;V i=0;char*p;p=base+i;*p=1;}',
    'void f(char*base){typedef volatile int V;typedef V W;W i=0;char*p;p=base+i;*p=1;}',
    'typedef volatile int V;void f(char*base){V i=0;char*p;p=base+i;*p=1;{typedef int V;V j=0;}}',
    'char*get(void);void f(void){char*p;p=get();*p=1;}',
    'char*global;void f(int i){char*p;p=global+i;*p=1;}',
    'void f(char*base){static int i;char*p;p=base+i;*p=1;}',
    'void f(char*base){extern int i;char*p;p=base+i;*p=1;}',
    'char*f(void){char*p;p="abc";return p;}',
    'void f(char**base,int i){char*p;p=*base+i;*p=1;}',
])
def test_unsafe_or_unproven_capture_has_no_expandable_reads(source):
    assert not allowed(source).get('p')


def test_invariant_capture_stays_available_and_macro_binding_does_not():
    source = 'void f(char*base,int i){char*p;p=base+i;*p=1;*p=2;}'
    assert allowed(source)['p'] == 2
    assert not allowed(source, ['i']).get('p')


def test_guarded_actual_expander_cannot_erase_stale_capture(tmp_path):
    from decompnet.permuter_gen import __file__ as script
    driver = textwrap.dedent('''
        import sys,random,copy
        sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2])
        from src import ast_util,randomizer
        from permuter_capture import protect_expand
        original_finder=randomizer.compute_read_locations
        guarded=protect_expand(randomizer.perm_expand_expr)
        for seed in range(40):
            ast=ast_util.parse_c('void f(char*base,int i){char*p;p=base+i;i=3;*p=2;}')
            fn=ast.ext[0];indices=ast_util.compute_node_indices(fn)
            try:guarded(fn,ast,indices,randomizer.Region.unbounded(),random.Random(seed))
            except randomizer.RandomizationFailure:pass
            text=ast_util.to_c(ast)
            assert '*p = 2' in text,text
        assert randomizer.compute_read_locations is original_finder
    ''')
    run = subprocess.run([str(toolchains.venv_bin(TOOL/'venv', 'python')), '-I', '-c', driver, str(TOOL), str(Path(script).parent)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize('partial', [False, True])
def test_generated_expansions_keep_captured_address_and_safe_elimination(tmp_path, partial):
    import shutil
    compiler = shutil.which('cc')
    if not compiler:
        pytest.skip('no host C compiler')
    from decompnet.permuter_gen import __file__ as script
    source = 'void f(char*base,int i){char*p;p=base+i;*p=1;'+('i=3;' if partial else '')+'*p=2;}'
    driver = textwrap.dedent('''
        import sys,random,json
        sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2])
        from src import ast_util,randomizer
        from permuter_capture import protect_expand
        source=sys.stdin.read();output=[]
        guarded=protect_expand(randomizer.perm_expand_expr)
        for seed in range(40):
            ast=ast_util.parse_c(source);fn=ast.ext[0];indices=ast_util.compute_node_indices(fn)
            guarded(fn,ast,indices,randomizer.Region.unbounded(),random.Random(seed))
            output.append(ast_util.to_c(ast))
        print(json.dumps(output))
    ''')
    run = subprocess.run([str(toolchains.venv_bin(TOOL/'venv', 'python')), '-I', '-c', driver,
                          str(TOOL), str(Path(script).parent)], input=source,
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    variants = json.loads(run.stdout)
    assignments = ['p = base + i;' in variant for variant in variants]
    assert all(assignments) if partial else not all(assignments)
    definitions = '\n'.join(variant.replace('void f(', f'void f{i}(') for i, variant in enumerate(variants))
    checks = '\n'.join(f'{{char b[8]={{0}}; f{i}(b,0); if(b[0]!=2 || b[3]!=0) return 1;}}'
                       for i in range(len(variants)))
    path = tmp_path/'check.c'
    path.write_text(definitions+'\nint main(void){'+checks+'return 0;}')
    binary = tmp_path/'check'
    subprocess.run([compiler, '-std=c90', '-pedantic-errors', '-O2', str(path), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
