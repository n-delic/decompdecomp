from decompnet.app import source_preparation as prep
from decompnet.app import source_units


def setup(tmp_path, text):
    (tmp_path/'game.c').write_text(text)
    cfg={'root':str(tmp_path),'header':'','mappings':'entry = caller @ game.c\nhelper = helper @ game.c\nprior = prior @ game.c',
         '_compiler_units':True,'_known_functions':['entry','helper','prior']}
    reader=prep.Reader(cfg)
    return reader,cfg


def test_real_private_call_unit_preserves_lines_static_bodies_macros_and_forward_types(tmp_path):
    text='#define MASK(x) ((x)&31)\nint prior(void) { return 7; }\nstatic int helper(int x)\n{\n return MASK(x);\n}\nint caller(int x)\n{ return helper(x); }\n'
    r,cfg=setup(tmp_path,text)
    pairs=source_units.variants(r,cfg,('entry','caller','game.c'),{'caller':'entry'},'int entry(int x){return helper(x);}', 'extern int helper(int x);')
    unit=next((s,h) for family,s,h in pairs if family=='cpu-private-source-unit')
    assert 'static int helper(int x)\n{\n return MASK(x);\n}' in unit[0]
    assert '#line 3 "reference.c"' in unit[0] and '#line 7 "reference.c"' in unit[0]
    assert '#define MASK(x) ((x)&31)' in unit[1]
    assert 'extern int helper' not in unit[1] and 'static int helper(int x)' in unit[1]
    assert 'prior(' not in unit[0]
    assert any(family=='cpu-source-predecessors' and 'int prior(void)' in s for family,s,h in pairs)


def test_no_unknown_bodies_or_disabled_architecture_context(tmp_path):
    r,cfg=setup(tmp_path,'int secret(int x){return x;}\nint caller(int x){return secret(x);}')
    assert source_units.variants(r,cfg,('entry','caller','game.c'),{'caller':'entry'},'int entry(int x){return secret(x);}','')==[]
    cfg['_compiler_units']=False
    assert source_units.variants(r,cfg,('entry','caller','game.c'),{},'int entry(void){}','')==[]


def test_alternative_sdk_file_is_one_coherent_unit_not_original_bodies(tmp_path):
    r,cfg=setup(tmp_path,'int helper(void){return 1;}\nint caller(void){return helper();}')
    (tmp_path/'new.c').write_text('int helper(void){return 2;}\nint caller(void){return helper()+2;}')
    pairs=source_units.variants(r,cfg,('entry','caller','new.c'),{'caller':'entry'},'int entry(void){return helper()+2;}','')
    assert pairs and all('return 1' not in s for _,s,_ in pairs)
    assert 'return 2' in pairs[0][1]


def test_failed_base_sdk_does_not_hide_valid_explicit_alternative(tmp_path):
    (tmp_path/'broken.c').write_text('int unrelated(void){return 1;}')
    (tmp_path/'new.c').write_text('int other(void){return 2;}')
    (tmp_path/'types.h').write_text('typedef int s32;')
    cfg={'root':str(tmp_path),'header':'','mappings':'entry = missing @ broken.c','contexts':'entry = other @ new.c | types.h'}
    r=prep.Reader(cfg)
    pairs=prep.prepare_variants(r,cfg,('entry','missing','broken.c'),{'missing':'entry'},'')
    assert pairs and 'int entry(void){return 2;}' in pairs[0][0]
    assert r.origins[pairs[0]]['file']=='new.c'


def test_static_dependency_of_context_body_is_preserved_without_duplicating_unit_bodies(tmp_path):
    text='static int scale(int x){return x*2;}\nstatic int helper(int x){return scale(x);}\nint caller(int x){return helper(x);}'
    r,cfg=setup(tmp_path,text)
    pairs=source_units.variants(r,cfg,('entry','caller','game.c'),{'caller':'entry'},'int entry(int x){return helper(x);}','extern int helper(int x);')
    assert pairs and 'static int scale(int x){return x*2;}' in pairs[0][2]
    from decompnet.forks.csource import find
    assert find(pairs[0][1],'helper') and not find(pairs[0][2],'helper')


def test_static_prototype_linkage_is_not_lost_when_definition_omits_static(tmp_path):
    text='static int helper(int x);\nint helper(int x){return x;}\nint caller(int x){return helper(x);}'
    r,cfg=setup(tmp_path,text)
    pairs=source_units.variants(r,cfg,('entry','caller','game.c'),{'caller':'entry'},'int entry(int x){return helper(x);}','extern int helper(int x);')
    assert pairs and 'static int helper(int x);' in pairs[0][2]


def test_context_reached_actual_table_definition_is_carried_not_fabricated(tmp_path):
    text='static int table[2]={3,7};\nstatic int helper(int x){return table[x];}\nint caller(int x){return helper(x);}'
    r,cfg=setup(tmp_path,text)
    pairs=source_units.variants(r,cfg,('entry','caller','game.c'),{'caller':'entry'},'int entry(int x){return helper(x);}','extern int helper(int x);')
    assert pairs and 'int table[2]={3,7};' in pairs[0][1]
    assert pairs[0][1].count('table[2]')==1


def test_context_external_call_keeps_actual_float_prototype(tmp_path):
    text='double external(double,int*);\nstatic int helper(int x){int e;return external(x,&e);}\nint caller(int x){return helper(x);}'
    r,cfg=setup(tmp_path,text)
    pairs=source_units.variants(r,cfg,('entry','caller','game.c'),{'caller':'entry'},'int entry(int x){return helper(x);}','extern int helper(int x);')
    assert pairs and 'double external(double,int*);' in pairs[0][2]


def test_matched_address_taking_owner_is_prioritized_without_a_direct_call(tmp_path):
    text='void entry(int);\nvoid install(void){ callback=(void *)entry; }\nvoid entry(int x){use(x);}'
    r,cfg=setup(tmp_path,text)
    cfg.update(mappings='entry = entry @ game.c',_known_functions=['entry','install'],_matched_functions=['install'])
    pairs=source_units.variants(r,cfg,('entry','entry','game.c'),{'install':'install'},'void entry(int x){use(x);}','void entry(int);')
    family,source,header=pairs[0]
    assert family=='cpu-address-source-unit'
    assert 'callback=(void *)entry' in source
    assert source.index('void entry(int);')<source.index('void install(')
    cfg['_matched_functions']=[]
    assert not any(f=='cpu-address-source-unit' for f,_,_ in source_units.variants(r,cfg,('entry','entry','game.c'),{'install':'install'},'void entry(int x){use(x);}','void entry(int);'))


def test_address_scanner_does_not_confuse_calls_or_literals_with_callback_values():
    assert source_units.address_reference('void install(){ table[0]=&entry; }','entry')
    assert not source_units.address_reference('void run(){ entry(1); puts("entry"); /* entry */ }','entry')


def test_saved_matched_callback_owner_can_come_from_another_source_unit(monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import repository_candidates
    rows=[{'name':'install','matched_source':'void install(void){ callback=&entry; }','meta':'{}','vram':8192}]
    net=SimpleNamespace(is_rom=lambda p:True,fork_slug=lambda s:None,store=SimpleNamespace(all=lambda *a:rows))
    net.project_store = lambda project:net.store
    monkeypatch.setattr(repository_candidates,'contexts',lambda *a:iter(['void entry(void);']))
    pairs=source_units.matched_owners(net,{'slug':'game','id':1,'arch':'mips'},{'id':1,'vram':4096,'name':'entry'},'void entry(void){ work(); }','void work(void);')
    assert pairs[0][0]=='cpu-address-source-unit' and 'callback=&entry' in pairs[0][1]
    assert pairs[0][1].startswith('void entry(void);')
    rows[0]['matched_source']='void install(void){ entry(); }'
    assert not source_units.matched_owners(net,{'slug':'game','id':1,'arch':'mips'},{'id':1,'vram':4096,'name':'entry'},'void entry(void){}','')
