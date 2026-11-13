import shutil
import subprocess

import pytest

from decompnet.app import draft_repairs, initializer_clusters, iterative_repairs, region_repairs, search_frontier
from decomprepair import allocation_repairs, allocator_call_guards, phase_lifetime_repairs


def test_pointer_guards_follow_goto_dominance_and_keep_four_identical_call_leaves():
    source = 'void f(int x){char data[24];char (*p)[24];p=&data;if(x)goto draw;use();draw:put((*p));}'
    proposals = allocator_call_guards.variants(source,'f')
    assert proposals and len(proposals)<=allocator_call_guards.LIMIT
    assert any(s.count('put((*p));')==4 for _,s in proposals)
    assert all('goto draw;' in s for _,s in proposals)
    for changed in [source.replace('p=&data;if(x)goto draw;','if(x)goto draw;p=&data;').replace('use();',''),
                    source.replace('draw:put','draw:p=other();put'),
                    source.replace('p=&data;','if(x)p=&data;'),
                    source.replace('put((*p));','escape(&p);put((*p));'),
                    source.replace('p=&data;','p=other();'),
                    source.replace('put((*p));','p++;put((*p));'),
                    source.replace('put((*p));','p+=1;put((*p));'),
                    source.replace('draw:','unknown:')]:
        assert not allocator_call_guards.variants(changed,'f')
    assert not allocator_call_guards.variants(source,'f','#define HIDDEN() p=0')
    assert not allocator_call_guards.variants(source.replace('char data[24];','char data[24]; int p;'),'f')


def test_mixed_initializers_permute_whole_group_without_new_reads_or_escaped_locals():
    source = 'void f(void){char data[24];char (*p)[24];void *q;char other[24];unsigned short y;int i;'
    source += 'y=40;i=0;p=&data;q=other;use(p,q,y,i);}'
    candidates = allocation_repairs.initialization_orders(source,'f')
    assert len(candidates)==23
    assert any('i=0;p=&data;y=40;q=other;' in c for c in candidates)
    assert all(c.count('use(p,q,y,i);')==1 for c in candidates)
    assert all('y=next();i=next();' in c for c in allocation_repairs.initialization_orders(source.replace('i=0','i=next()').replace('y=40','y=next()'),'f'))
    assert not allocation_repairs.initialization_orders('void f(void){int i;int *p;p=&i;i=0;}','f')


def test_initializer_call_cluster_joins_address_and_reorders_independent_mixed_locals():
    source = ('void f(void *out){char data[24];char (*p)[24];void *q;char other[24];'
              'unsigned short y;int i;int a;int b; p=&data;a=1;b=2;'
              'put((i=0,y=40,q=other,out),a,b);use(p,q,y,i);}')
    candidates = initializer_clusters.variants(source,'f')
    assert any('y = 40;\ni = 0;\np=&data;' in c for _,c in candidates)
    assert any('put(out,a,b)' in c.replace(' ','') for _,c in candidates)
    assert not initializer_clusters.variants(source.replace('a,b);','i,b);'),'f')
    assert not initializer_clusters.variants(source.replace('q=other','q=next()'),'f')
    # An intervening declaration may shadow the object whose address was taken.
    shadowed = source.replace('p=&data;a=1;b=2;','p=&data;char data[24];a=1;b=2;')
    assert all(c.count('p=&data;')==1 and c.index('p=&data;')<c.rindex('char data[24];')
               for _,c in initializer_clusters.variants(shadowed,'f'))


def test_phase_splits_require_dominance_and_can_pair_with_disjoint_merge():
    source = 'int f(int x){int width;int first;int second;width=read();first=width;use(first);'
    source += 'second=read();use(second);if(x){width=7;use(width);}return 0;}'
    proposals = phase_lifetime_repairs.variants(source,'f')
    assert any(family=='split_integer_phase' for family,_ in proposals)
    assert any(family=='paired_integer_lifetimes' for family,_ in proposals)
    assert not phase_lifetime_repairs.variants(source.replace('return 0;','HIDDEN;return 0;'),'f','#define HIDDEN width=other')
    crossing = 'int f(int x){int width;width=read();if(x)width=7;use(width);return 0;}'
    assert not phase_lifetime_repairs.variants(crossing,'f')


def rows(losses):
    return [('r' if i//16 in losses else ' ', 'move $s0, $a0',
             'move $s1, $a0' if i//16 in losses else 'move $s0, $a0') for i in range(48)]


def test_regional_detour_crosses_scalar_ceiling_but_cannot_accept_a_match():
    before, after = rows({0}), rows({1,2})
    node = dict(ceiling=100,region_baseline=region_repairs.snapshot(before))
    assert iterative_repairs.regional_detour(node,dict(score=500,rows=after))
    assert not iterative_repairs.regional_detour(node,dict(score=801,rows=after))
    assert not iterative_repairs.regional_detour(node,dict(score=500,rows=[('bad',)]))
    assert not iterative_repairs.regional_detour(node,dict(score=500,rows=after+[('>','','nop')]))
    state = dict(pending=[dict(source='start',depth=0,ceiling=100)],next=[],seen=['start'])
    visited = []
    def evaluate(source):
        visited.append(source)
        return dict(score={'start':100,'bridge':500,'end':0}[source],rows=before if source=='start' else after,
                    matched=source=='end')
    def generate(source, report):
        return {'start':[('split','bridge')],'bridge':[('reuse','end')]}.get(source,[])
    assert iterative_repairs.advance(state,evaluate,generate,lambda:False)
    assert visited==['start','bridge','end']
    frontier = search_frontier.Frontier()
    frontier.offer('numeric',100,before,baseline=before)
    frontier.offer('regional',500,after,baseline=before)
    for i in range(15):frontier.offer(str(i),101+i,before,baseline=before)
    assert 'regional' in {n['source'] for n in frontier.nodes}


def test_shared_pipeline_exposes_new_families_in_both_architectures():
    source='void f(void){char data[24];char (*p)[24];p=&data;goto draw;draw:put((*p));}'
    for arch in ('mips','ppc'):
        labels = {}
        draft_repairs.variants(source,'f',arch,labels=labels,limit=None)
        assert 'balanced_pointer_calls' in labels.values()
    assert {'balanced_pointer_calls','initialization_cluster','paired_integer_lifetimes'} <= set(draft_repairs.AGENT_FAMILIES)


def test_pointer_branches_preserve_runtime_call_order_across_loops_and_gotos(tmp_path):
    cc = shutil.which('cc')
    if not cc:pytest.skip('Host C compiler required')
    source = ('static int trace;void put(char *p){trace=trace*10+*p;} '
              'int work(int x){char data[1];char (*p)[1];int i;p=&data;data[0]=2;'
              'for(i=0;i<3;i++){if(x)goto draw;put(data);draw:put((*p));}return trace;}')
    proposals = allocator_call_guards.variants(source,'work')
    assert proposals
    for index,(_,candidate) in enumerate(proposals):
        path, binary = tmp_path/f'{index}.c', tmp_path/f'{index}'
        path.write_text(candidate+'\nint main(void){trace=0;if(work(1)!=222)return 1;trace=0;return work(0)!=222222;}')
        subprocess.run([cc,'-std=c99','-O2',str(path),'-o',str(binary)],check=True,capture_output=True)
        subprocess.run([str(binary)],check=True,timeout=5)


def test_cluster_and_paired_lifetime_variants_preserve_observable_calls_and_values(tmp_path):
    cc = shutil.which('cc')
    if not cc:pytest.skip('Host C compiler required')
    cluster = ('static int trace;int read_value(void){trace=trace*10+1;return 3;}'
               'void put(void *out,int a,int b){trace=trace*10+2;}'
               'int work(void *out){char data[24];char other[24];char (*p)[24];void *q;'
               'unsigned short y;int i;int a;int b;data[0]=7;other[0]=9;p=&data;'
               'a=read_value();b=read_value();put((i=0,y=40,q=other,out),a,b);'
               'return (*p)[0]+*(char *)q+y+i+a+b+trace;}')
    phase = ('static int trace;void use(int x){trace=trace*10+x;}'
             'int work(int x){int width;int first;int second;width=3;first=width;use(first);'
             'second=4;use(second);if(x){width=7;use(width);}return trace;}')
    cases = [(initializer_clusters.variants(cluster,'work'),'int main(void){return work(0)!=174;}'),
             (phase_lifetime_repairs.variants(phase,'work'),
              'int main(void){trace=0;if(work(1)!=347)return 1;trace=0;return work(0)!=34;}')]
    for group,(proposals,main) in enumerate(cases):
        assert proposals
        for index,(_,candidate) in enumerate(proposals):
            path, binary = tmp_path/f'{group}-{index}.c', tmp_path/f'{group}-{index}'
            path.write_text(candidate+main)
            subprocess.run([cc,'-std=c99','-O2',str(path),'-o',str(binary)],check=True,capture_output=True)
            subprocess.run([str(binary)],check=True,timeout=5)
