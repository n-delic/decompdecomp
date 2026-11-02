from decompnet.app import equal_value_flow as values


def proposals(body):
    return values.variants('typedef unsigned short u16; int f(int cond){u16 a;u16 b;u16 c;'+body+'return b;}','f')


def test_copy_survives_branches_and_unescaped_calls():
    assert any('b = a + 1;' in s for _,s in proposals('b=4;a=b;if(cond){use(a);}else{other();}++b;'))
    assert not proposals('b=4;if(cond){a=b;}++b;')
    assert not proposals('b=4;a=b;if(cond){a=8;}++b;')
    assert not proposals('b=4;a=b;use(&a);++b;')


def test_decrement_restore_tracks_modular_equality_on_every_path():
    code='b=4;a=--b;if(cond){use(a);}++b;c=--b;if(cond){use(c);}++b;'
    assert any('b = a + 1;' in s for _,s in proposals(code))
    assert not proposals('b=4;a=b;while(cond){++a;}++b;')
    assert not proposals('b=4;a=b;goto skip;a=9;skip:++b;')==[]  # unreachable write cannot invalidate equality


def test_unknown_writes_macros_and_signed_offsets_are_not_proofs():
    assert not proposals('b=4;a=b;b+=2;++b;')
    assert not proposals('b=4;a=b;if(++a){use(a);}++b;')
    source='int f(void){int a;int b;b=4;a=--b;++b;--b;++b;return b;}'
    assert len(values.variants(source,'f'))==1  # adjacent equal copy only; later signed offset is discarded
    assert not values.variants('int f(void){int a;int b;b=1;a=b;HIDDEN();++b;return b;}','f','#define HIDDEN() a=2')


def test_one_shot_latches_do_not_destroy_equal_values():
    code='b=4;a=--b;if(cond){use(a);}do{do{++b;c=b;}while(0);}while(0);--b;if(cond){use(b);}++b;'
    assert any('b = a + 1;' in s for _,s in proposals(code))


def test_synthetic_branch_paths_and_wraparound_execute_identically(tmp_path):
    import shutil
    import subprocess

    import pytest
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host C compiler unavailable')
    body='b=0;a=--b;if(cond){use(a);}do{++b;}while(0);c=--b;if(cond){use(c);}++b;'
    source='typedef unsigned short u16;void use(int x){}\nint f(int cond){u16 a;u16 b;u16 c;'+body+'return b;}\nint main(void){return f(0)!=0||f(1)!=0;}'
    candidates=values.variants(source,'f')
    assert candidates
    for i,text in enumerate([source]+[s for _,s in candidates]):
        path=tmp_path/f'{i}.c';path.write_text(text);binary=tmp_path/f'{i}'
        subprocess.run([cc,'-O2',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=20)
        subprocess.run([str(binary)],check=True,timeout=5)


def test_taken_address_parentheses_and_loop_join_do_not_prove_equality():
    assert not proposals('b=4;a=b;use(&(a));++b;')
    assert not proposals('b=4;a=b;while(cond){b+=1;}++b;')
    assert all('}++b;' in s for _,s in proposals('b=4;a=b;if(cond){b+=1;}else{b+=2;}++b;'))


def test_macro_expression_reads_are_allowed_but_aliases_are_not():
    source='int f(void){int a;int b;b=1;a=b;if(READ(2)){use(b);}++b;return b;}'
    assert values.variants(source,'f','#define READ(x) (*(int *)(address+(x)))')
    assert not values.variants(source,'f','#define READ(x) OTHER(x)\n#define OTHER(x) (a=2)')


def test_value_flow_rules_invalidate_cached_searches():
    from decompnet.claim_stalls import repair_rules
    assert any(name=='equal_value_flow.py' and digest!='unavailable' for name,digest in repair_rules())


def test_complement_prefers_value_flow_after_scope():
    from decompnet.app import repair_selection
    assert repair_selection.order(['other','cfg_equal_value_increment'],['loop_weight_scope'])[0]=='cfg_equal_value_increment'


def test_one_extra_move_is_lifetime_guidance_not_arbitrary_control_flow():
    from decompnet.app import allocation_evidence, repair_ranking
    rows=[('r','move $s1, $a0','move $s2, $a0'),('>','','move $a2, $s3')]
    assert allocation_evidence.diagnose(rows)['copy_near_miss']
    assert repair_ranking.order(['branch_call','cfg_equal_value_increment'],rows)[0]=='cfg_equal_value_increment'
    assert not allocation_evidence.diagnose([('>','','addiu $a2, $s3, 1')])['copy_near_miss']
