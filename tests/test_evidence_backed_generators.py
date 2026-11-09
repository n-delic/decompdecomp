import shutil
import subprocess

import pytest

from decompnet.app import draft_repairs, pattern_repairs, repair_ranking, scalar_normalization_scope
from decompnet.app.repair_preflight import drop_argument_allowed


def test_unrelated_macro_does_not_hide_known_arity():
    source='#define OTHER (record->value)\nvoid send(unsigned char, void *);\nvoid f(void *p){send(1,p);}'
    assert not drop_argument_allowed(source,'send','1,p')
    candidates=draft_repairs.variants(source,'f','mips')
    assert candidates and all('send(1);' not in c for c in candidates)
    assert drop_argument_allowed('void send(unsigned char);void f(void *p){send(1,p);}','send','1,p')


@pytest.mark.parametrize('source,args',[
    ('#define X 1,2\nvoid send(int,int);','X'),
    ('#define word void\nvoid send(word);','1'),
    ('#define send another\nvoid send(int,int);','1,2'),
    ('#include "api.h"\nvoid send(int,int);','1,2'),
    ('#if FEATURE\nvoid send(int,int);\n#endif','1,2'),
    ('void send();','1,2'),('void send(word,word);','1,2'),
    ('void send(int,...);','1,2'),('void send(int);void send(int,int);','1,2'),
    ('void f(){int (*send)();send(1,2);}','1,2'),
])
def test_opaque_arity_evidence_stays_with_compiler(source,args):
    assert drop_argument_allowed(source,'send',args)


def test_narrow_temporary_is_a_c89_statement_and_preserves_else(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host compiler unavailable.')
    source='typedef unsigned char u8;void f(u8 *p,int c){*p=0;if(c)*(u8*)p=(u8)257;else *p=9;}'
    choices=[c for family,c in pattern_repairs.variants(source,'f') if family=='typed_temporary']
    assert choices and all('{ u8 __repair_narrow' in c for c in choices)
    for i,candidate in enumerate(choices):
        path=tmp_path/f'case{i}.c';binary=tmp_path/f'case{i}'
        path.write_text(candidate+'\nint main(void){u8 p;f(&p,0);if(p!=9)return 1;f(&p,1);return p!=1;}')
        subprocess.run([cc,'-std=c89','-pedantic-errors',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=20)
        subprocess.run([str(binary)],check=True,capture_output=True,timeout=10)


def test_zero_subtraction_normalization_matches_unary_form():
    source='int f(int a,int b){int offset;int result;int saved;offset=a;result=b;offset=(offset<0)?(0-offset-result):offset;return offset;}'
    variants=scalar_normalization_scope.variants(source,'f')
    assert len(variants)==1 and 'saved=-saved-result' in variants[0][1]
    assert not scalar_normalization_scope.variants(source.replace('0-offset','1-offset'),'f')
    assert not scalar_normalization_scope.variants(source.replace('0-offset','0-result'),'f')
    assert not scalar_normalization_scope.variants(source.replace('int saved;',''),'f')


def test_guarded_layouts_precede_generic_probes_and_keep_every_family():
    families=['source_lines','integer_array_pointer_phase','inferred_record_layout','typed_temporary','m2c_refinement']
    ordered=repair_ranking.order(families,[])
    assert ordered.index('integer_array_pointer_phase')<ordered.index('source_lines')
    assert ordered.index('inferred_record_layout')<ordered.index('typed_temporary')
    assert set(ordered)==set(families)
    # Structural ranking is initial advice, not an override of measured deferral every trial.
    assert repair_ranking.guarded_first(families)==families


def test_zero_subtraction_scope_preserves_finite_integer_results(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host compiler unavailable.')
    source='int f(int a,int b){int offset;int result;int saved;offset=a;result=b;offset=(offset<0)?(0-offset-result):offset;return offset;}'
    candidate=scalar_normalization_scope.variants(source,'f')[0][1].replace('int f(','int g(')
    path=tmp_path/'scope.c';binary=tmp_path/'scope'
    path.write_text(source+'\n'+candidate+'\nint main(void){int a,b;for(a=-100;a<=100;a++)for(b=-50;b<=50;b++)if(f(a,b)!=g(a,b))return 1;return 0;}')
    subprocess.run([cc,'-std=c89','-pedantic-errors','-O2',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=20)
    subprocess.run([str(binary)],check=True,capture_output=True,timeout=10)
