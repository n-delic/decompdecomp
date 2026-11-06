from decompnet.app import profile_hypotheses
from decompnet.app.helper_call_diagnostics import suggestions
from decompnet.app.source_type_context import referenced


def test_helper_alias_only_when_every_other_instruction_agrees():
    rows=[[' ','move $a0, $v0','move $a0, $v0'],['i','jal func_80001000','jal ?__ull_div'],['i','jal func_80001000','jal ?__ull_div']]
    assert '__ull_div → func_80001000' in suggestions(rows)[0]
    assert not suggestions(rows+[['i','move $a1, $v1','move $a1, $v0']])
    assert not suggestions(rows+[['i','jal func_80002000','jal ?__ull_div']])
    assert not suggestions([['i','jal game_fn','jal ?unknown']])
    assert not suggestions([['>','','jal ?__ull_div']])


def test_reference_types_and_multiline_macros_keep_union_layout_without_other_objects():
    text='typedef unsigned short word;\ntypedef union {word value;struct {char bank,page;}parts;} Page;\nint unrelated;\n#define VALID(p) \\\n ((p).value > 1)\nint original(Page*p){return VALID(*p);}\n'
    got=referenced(text,'int f(Page*p){return VALID(*p);}','')
    assert 'typedef union' in got and 'typedef unsigned short word' in got and '#define VALID' in got
    assert 'int unrelated' not in got and 'int original' not in got
    assert referenced(text,'int f(void){return 0;}','')==''
    assert 'typedef union' not in referenced(text,'Page p;','typedef union {int x;} Page;')


def test_neighbour_profiles_are_bounded_hypotheses_of_the_same_toolchain():
    class Store:
        def all(self,query,*args):
            assert "status='matched'" in query and 'best_score=0' in query and 'LIMIT 8' in query
            return [{'vram':10},{'vram':20},{'vram':30}]
    class Net:
        store=Store()
        def cfg(self,p):return {'compiler':{'toolchain':'ido-5.3','flags':['-O2']},'compiler_ranges':[
            {'start':10,'end':11,'toolchain':'ido-5.3','flags':['-O1']},
            {'start':20,'end':21,'toolchain':'gcc','flags':['-O1']},
            {'start':30,'end':31,'toolchain':'ido-5.3','flags':['-O1']}]}
    assert profile_hypotheses.neighbours(Net(),{'id':1},{'vram':15},{'toolchain':'ido-5.3','flags':['-O2']})==[['-O1']]


def test_source_preparation_carries_referenced_types_with_a_prepared_header(tmp_path):
    from decompnet.app import source_preparation as prep
    from tests.test_source_preparation import config
    cfg=config(tmp_path)
    (tmp_path/'types.h').write_text('typedef unsigned short word;\n')
    (tmp_path/'game.c').write_text('typedef union {word value;struct{char bank,page;}parts;}Page;\n#define READ(p) ((p)->value)\nint original(Page*p){return READ(p);}\n')
    source,header=prep.prepare(prep.Reader(cfg),cfg,('f','original','game.c'),{'original':'f'},'')
    assert 'int f(Page*p)' in source and 'typedef union' in header and '#define READ' in header
