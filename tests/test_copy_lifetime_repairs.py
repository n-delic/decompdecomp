from decompnet.app import copy_lifetime_repairs as repairs
from decompnet.app import debug_boundary_repairs, draft_repairs, local_experiments, repair_ranking


def test_dead_source_reset_handles_initialized_copy_and_comma_locals():
    source = 'int f(int input){int a,b; a=input; {int saved=a; if(saved<0)saved+=127; saved>>=7; b=saved;} a=2; return b+a;}'
    variants = repairs.variants(source, 'f')
    assert any('int saved=a;a=0;' in text for family, text in variants if family == 'dead_copy_source')
    assert source not in [text for _, text in variants]
    assert 'dead_copy_source' in repairs.hint(source, 'f')


def test_copy_reset_rejects_live_paths_escapes_shadows_and_unbraced_copies():
    bodies = [
        'int a,b; a=1; b=a; if(b)use(a); return b;',
        'int a,b; a=1; while(a){b=a; use(b);} return 0;',
        'int a,b; a=1; use(&a); b=a; return b;',
        'int a,b; a=1; use(&(a)); b=a; return b;',
        'int a,b; a=1; if(a)b=a; return b;',
        'int a,b; a=1; {int a; a=2; b=a;} return b;',
        'volatile int a; int b; a=1; b=a; return b;',
        'int a,b; a=1; b=a; goto again; again: return a;',
    ]
    for body in bodies:
        assert not any(f == 'dead_copy_source' for f, _ in repairs.variants('int f(){'+body+'}', 'f')), body


def test_local_object_aliases_remain_opaque_and_scalar_macro_names_are_excluded():
    source = 'int f(){\n#define slots record.items\n int a,b; a=1; b=a; return b;}'
    assert any('b=a;a=0;' in text and '#define slots record.items' in text for _, text in repairs.variants(source,'f'))
    for directive in ('#define use_a a', '#define slots(x) do{x;}while(0)', '#define slots a\\'):
        assert not repairs.variants(source.replace('#define slots record.items', directive), 'f')


def test_divisor_flag_keeps_arithmetic_and_does_not_rewrite_unbraced_condition():
    source = 'int f(){int divisor; divisor=read(); divisor += (divisor == 0); return 50/divisor;}'
    assert any('int __repair_divisor_zero=(divisor==0);divisor+=__repair_divisor_zero;' in text
               for f,text in repairs.variants(source,'f') if f == 'divisor_flag_split')
    assert not repairs.variants(source.replace('divisor +=', 'if(test()) divisor +='),'f')


def test_regional_load_reuses_only_adjacent_local_array_value():
    source = 'int f(int i){int scratch; int values[4]; scratch=values[i]; if(values[i]<3)return scratch; return 0;}'
    assert any('if(scratch<3)' in text for f,text in repairs.variants(source,'f') if f=='regional_load_reuse')
    for replacement in ('scratch=values[i]; mutate();', 'scratch=values[i]; i++;'):
        assert not any(f=='regional_load_reuse' for f,_ in repairs.variants(source.replace('scratch=values[i];',replacement),'f'))
    assert not any(f=='regional_load_reuse' for f,_ in repairs.variants(source.replace('int values[4];',''),'f'))


def test_debug_boundaries_are_gated_bounded_and_leave_for_headers_comments_intact():
    source = 'void f(){for(i=0;i<3;i++){x/=y;if(x)use(x);} /* keep */ finish();}'
    assert not debug_boundary_repairs.variants(source,'f', ['-O2','-g0'])
    variants = debug_boundary_repairs.variants(source,'f',['-O2','-g3'])
    assert any('x/=y;\nif(x)' in text for _,text in variants)
    assert all('for(i=0;i<3;i++)' in text and '/* keep */' in text for _,text in variants)
    assert len(variants)<=debug_boundary_repairs.LIMIT


def test_new_families_reach_shared_draft_and_agent_pipelines():
    source = 'int f(){int a,b; a=1; b=a; return b;}'
    labels = {}
    choices = draft_repairs.variants(source,'f','mips',flags=['-g3'],labels=labels,limit=None)
    assert any(labels[c]=='dead_copy_source' for c in choices)
    assert all(f in local_experiments.FAMILIES for f in repairs.FAMILIES)
    rows = [('r','bgez $v0, 0x20','bgez $a3, 0x20')]
    assert repair_ranking.order(['declaration_order','dead_copy_source'],rows)[0]=='dead_copy_source'


def test_divisor_scope_requires_dead_original_and_preserves_division_semantics():
    source = 'int f(int n,int q){int d;d=n/128;d+=(d==0);q/=d;return q;}'
    assert any('int __repair_divisor_value=n/128;' in text for f,text in repairs.variants(source,'f') if f=='divisor_value_scope')
    for changed in (source.replace('return q;', 'return q+d;'), source.replace('d=n/128;', 'if(q)d=n/128;')):
        assert not any(f=='divisor_value_scope' for f,_ in repairs.variants(changed,'f'))


def test_normalization_moves_to_copy_only_before_scratch_redefinition():
    source = 'int f(int n){int d,origin;origin=n;d=origin;if(d<0)d+=127;d>>=7;d+=(d==0);{int saved=d;d=n<8;return saved+d;}}'
    choices = [(f,s) for f,s in repairs.variants(source,'f') if f=='divisor_normalization_copy']
    assert len(choices)==1
    assert '{int saved=origin;if(saved<0)saved+=127;saved>>=7;d=(saved==0);saved+=d;' in choices[0][1]
    assert any('saved=origin;origin=0;' in s for f,s in repairs.variants(choices[0][1],'f') if f=='dead_copy_source')
    for changed in (source.replace('d=n<8;', 'use(d);d=n<8;'), source.replace('+=127', '+=126')):
        assert not any(f=='divisor_normalization_copy' for f,_ in repairs.variants(changed,'f'))


def test_paired_search_prioritizes_copy_fix_after_structure_recovery():
    from decompnet.app import residual_followups, search_frontier
    before = [('r','move $v0, $a0','move $v0, $a1'), ('o','nop','addu $t0, $a0, $a1')]
    after = [('r','move $v0, $a0','move $v0, $a1'), (' ','nop','nop')]
    frontier = search_frontier.Frontier()
    frontier.offer('numeric',10,before,arch='mips',baseline=before,baseline_score=10)
    frontier.offer('recovered',15,after,arch='mips',baseline=before,baseline_score=10)
    assert any(n['source']=='recovered' for n in frontier.nodes)
    proposals = [('declaration_order','shuffle'),('dead_copy_source','copy'),('divisor_normalization_copy','normalize')]
    assert residual_followups.select(proposals,before,after,'mips')==[('dead_copy_source','copy'),('divisor_normalization_copy','normalize')]


def test_copies_cannot_hide_pointer_or_qualified_shadows():
    for declaration in ('int *a;', 'const int a;'):
        source = 'int f(){int x,a; a=1; { '+declaration+' x=a; return x;}}'
        assert not any(f=='dead_copy_source' for f,_ in repairs.variants(source,'f'))


def test_normalization_cannot_change_value_read_by_scratch_redefinition():
    source = 'int f(){int d,origin;origin=read();d=origin;if(d<0)d+=127;d>>=7;d+=(d==0);{int saved=d;d=d+2;return saved+d;}}'
    assert not any(f=='divisor_normalization_copy' for f,_ in repairs.variants(source,'f'))
    source = 'int f(int n){int d;d=n/128;d+=(d==0);d/=d;return 0;}'
    assert not any(f=='divisor_value_scope' for f,_ in repairs.variants(source,'f'))


def test_shared_pipeline_preserves_alias_directives_and_original_line_layout():
    source = 'int f(){\n#define slots record.items\n int a,b; a=read(); {int saved=a;return saved;} }'
    labels = {}
    choices = draft_repairs.variants(source,'f','mips',flags=['-g3'],labels=labels,limit=None)
    assert any('int saved=a;a=0;' in s and '#define slots record.items\n' in s
               for s in choices if labels[s]=='dead_copy_source')


def test_control_flow_cannot_be_hidden_in_external_macros():
    source = '#define again goto top\nint f(){int a,b; a=1; top: b=a; again; return b;}'
    assert not repairs.variants(source,'f')


def test_out_of_scope_declaration_does_not_license_writes_to_global():
    for body in ('int b; {int a;a=1;use(a);} b=a; return b;',
                 'int b; struct {int a;} record; b=a; return b;'):
        assert not any(f=='dead_copy_source' for f,_ in repairs.variants('int a; int f(){'+body+'}','f'))


def test_debug_probes_respect_last_flag_and_line_sensitive_macros():
    source = 'int f(){int a; a=1;return a;}'
    assert not debug_boundary_repairs.variants(source,'f',['-g3','-g0'])
    assert not debug_boundary_repairs.variants(source,'f',['-g3'], '#define WHERE __LINE__')
    assert not debug_boundary_repairs.variants('#line 20\n'+source,'f',['-g3'])
