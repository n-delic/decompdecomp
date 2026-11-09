from decompnet.app import draft_repairs, repair_ranking
from decompnet.app import record_lifetime_repairs as repairs
from decompnet.app.structured_loop_repairs import variants as loops


def test_existing_record_forwards_only_one_scalar_lifetime():
    source='''int f(int a,int b){struct {int x,y;} delta; int t; int out;
    t=a-b; out=a+1; delta.y=t; out=out-t; consume(&delta); t=7; return out+t;}'''
    candidates=repairs.variants(source,'f')
    candidate=next(c for f,c in candidates if f=='record_value_forwarding')
    assert 'delta.y=a-b;' in candidate
    assert 'out=out-delta.y;' in candidate
    assert 't=7; return out+t;' in candidate
    assert 'delta.y=t' not in candidate
    labels={}
    assert candidate in draft_repairs.variants(source,'f','mips',labels=labels,limit=None)
    assert labels[candidate]=='record_value_forwarding'


def test_record_does_not_invent_types_or_cross_observable_boundaries():
    source='void f(int a){struct {int x;} rec; int t; int out; t=a; rec.x=t; out=t+1; use(out);}'
    assert any(f=='record_value_forwarding' for f,_ in repairs.variants(source,'f'))
    for changed in [source.replace('int x','short x'), source.replace('struct {','union {'),
                    source.replace('t=a;', 'escape(&rec); t=a;'), source.replace('rec.x=t;', 'call(); rec.x=t;'),
                    source.replace('rec.x=t;', 'out=rec.x; rec.x=t;'),
                    source.replace('int t;', 'volatile int t;'),
                    source.replace('out=t+1;', 'out=t+1; rec.x=0;'),
                    source.replace('int out;', 'int out; int *p; p=&t;')]:
        assert not any(f=='record_value_forwarding' for f,_ in repairs.variants(changed,'f')), changed


def test_narrow_definition_keeps_later_wide_index_lifetime():
    source='int f(int x){int t; int y; t=(short)x; y=t*8; t=x+65536; return t+y;}'
    candidate=next(c for f,c in repairs.variants(source,'f') if f=='narrow_definition_lifetime')
    assert 'short __repair_narrow_t;' in candidate
    assert 'y=__repair_narrow_t*8;' in candidate
    assert 't=x+65536; return t+y;' in candidate
    # A value still read beyond a branch boundary cannot be narrowed by textual substitution.
    assert not repairs.variants('int f(int x){int t; t=(short)x; if(x) use(t); return t;}','f')
    assert not repairs.variants(source.replace('t=(short)x','t=x'),'f')
    assert not repairs.variants(source.replace('y=t*8','t+=30000; y=t*8'),'f')


def test_zero_sink_requires_definite_assignment_and_preserves_following_else():
    source='int f(int a){int cell; cell=0; if(a){cell=a+1;} return cell;}'
    candidate=next(c for f,c in repairs.variants(source,'f') if f=='sink_zero_initialization')
    assert 'if(a){cell=a+1;} else { cell=0; }' in candidate
    for changed in [source.replace('cell=a+1','cell=cell+1'),source.replace('if(a)','if(cell)'),
                    source.replace('cell=a+1;','if(a>2)cell=a+1;'),
                    source.replace('} return','} else {use(cell);} return')]:
        assert not any(f=='sink_zero_initialization' for f,_ in repairs.variants(changed,'f'))


def test_structured_loop_keeps_nested_switch_breaks_and_internal_gotos():
    source='''int f(int n){int i;i=0;again:
    switch(i){case 0: if(n)goto inner; break; case 1: inner: n--; break;}
    i++; if(i<n) goto again; return n;}'''
    candidate=next(c for _,c in loops(source,'f'))
    assert 'do {' in candidate and '} while (i<n);' in candidate
    assert 'goto inner;' in candidate and 'inner:' in candidate
    assert 'again:' not in candidate
    assert 'break;' in candidate
    assert not loops(source.replace('i=0;', 'i=0; if(n)goto inner;'),'f')
    assert loops(source.replace('i++;', 'i++; if(n<0)goto outside;').replace('return n;', 'outside: return n;'),'f')


def test_loop_refuses_outer_break_continue_and_other_switch_entries():
    assert not loops('void f(int n){while(n){L: n--; if(n)break; if(n)goto L;}}','f')
    assert not loops('void f(int n){while(n){L: n--; if(n)continue; if(n)goto L;}}','f')
    assert not loops('void f(int n){switch(n){case 0:L:n--;case 1:n++;if(n)goto L;}}','f')
    source='void f(int n){L:n--;if(n){goto L;}}'
    assert loops(source,'f')


def test_register_only_ranking_prioritizes_lifetime_families_without_exclusion():
    families=['other','typed_temporary','structured_backedge',*repairs.FAMILIES]
    ordered=repair_ranking.order(families,[('r','subu $a0, $a0, $a1','subu $a0, $a2, $a0')])
    assert ordered[:3]==list(repairs.FAMILIES)
    assert set(ordered)==set(families)


def test_narrowing_does_not_change_size_unsigned_arithmetic_or_nested_updates():
    base='int f(int x){int t; int y; t=(short)x; y=t*8; t=x; return t+y;}'
    for changed in [base.replace('y=t*8','y=sizeof(t)'),
                    base.replace('int t;', 'unsigned int t;').replace('(short)', '(unsigned short)'),
                    base.replace('y=t*8','y=(t=70000)'),
                    base.replace('t=(short)x','t=(short)x - (short)(x+1)')]:
        assert not any(f=='narrow_definition_lifetime' for f,_ in repairs.variants(changed,'f'))


def test_record_types_in_header_and_unrelated_volatile_global():
    header='extern volatile int timer; typedef struct {int x;} Record;'
    source='int f(int a){Record rec; int t; int out;t=a;rec.x=t;out=t+1;return out;}'
    assert any(f=='record_value_forwarding' for f,_ in repairs.variants(source,'f',header))
    assert not repairs.variants(source,'f',header.replace('int x','volatile int x'))


def test_generated_loop_width_and_zero_candidates_preserve_host_execution(tmp_path):
    import ctypes
    import shutil
    import subprocess

    import pytest
    compiler=shutil.which('cc')
    if not compiler:
        pytest.skip('needs a host C compiler')
    sources=[
        ('int f(int x){int t; int y; t=(short)x; y=t*8; t=x+65536; return t+y;}',
         'narrow_definition_lifetime', repairs.variants),
        ('int f(int a){int cell;cell=0;if(a){cell=a+1;}return cell;}',
         'sink_zero_initialization', repairs.variants),
        ('int f(int n){int i;i=0;L:switch(i&1){case 0:n+=2;break;default:n-=1;break;}i++;if(i<4)goto L;return n;}',
         'structured_backedge',loops),
    ]
    for index,(source,family,generate) in enumerate(sources):
        generated=next(s for f,s in generate(source,'f') if f==family)
        path=tmp_path/f'check{index}.c'
        path.write_text(source.replace(' f(', ' original(')+'\n'+generated)
        library=tmp_path/f'check{index}.so'
        subprocess.run([compiler,'-shared','-fPIC','-O2',str(path),'-o',str(library)],check=True,capture_output=True)
        loaded=ctypes.CDLL(str(library))
        for function in (loaded.f,loaded.original):
            function.argtypes=[ctypes.c_int];function.restype=ctypes.c_int
        for value in [-100000,-65536,-32769,-32768,-1,0,1,32767,32768,65535,100000]:
            assert loaded.f(value)==loaded.original(value),(family,value)
