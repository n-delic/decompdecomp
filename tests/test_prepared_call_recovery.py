from decompnet.app import allocation_evidence, allocation_search, donor_structure, draft_repairs, repair_ranking
from decomprepair import prepared_call_repairs

SOURCE = '''extern int classify(unsigned char *);
extern unsigned char buffer[];
void f(unsigned char *base, int offset) {
 int code;
 unsigned char *out;
 { int status = classify(base + offset);
 out = buffer;
 code = status; }
 consume(out, code);
}'''
ROWS = [('o','move $a1, $v0','lui $a2, 0x8010'),
        ('o','lui $a2, 0x8010','addiu $a2, $a2, 32'),
        ('o','addiu $a2, $a2, 32','move $a1, $v0')]


def test_prepared_return_bundle_preserves_real_call_and_pointer_read():
    variants=prepared_call_repairs.variants(SOURCE,'f',arch='mips')
    assert len(variants)==1
    family,text=variants[0]
    assert family=='prepared_return_scope'
    assert 'unsigned char * __repair_call_argument;' in text
    assert '__repair_call_argument = base + offset;' in text
    assert 'code = classify(__repair_call_argument);\n    } while (0);\n    out = buffer;' in text
    assert 'status' not in text
    assert text.count('classify(')==2  # prototype and exactly one call
    assert not prepared_call_repairs.variants(text,'f',arch='mips')


def test_assigned_call_and_typed_argument_are_separate_bounded_choices():
    source='extern int classify(int); void f(int x) { int code; code = classify(x + 1); use(code); }'
    choices=prepared_call_repairs.variants(source,'f',arch='mips')
    assert {f for f,_ in choices}=={'assigned_call_scope','prepared_call_scope'}
    assert all(c.count('classify(')==2 for _,c in choices)
    assert all('do {' in c and 'while (0);' in c for _,c in choices)


def test_reject_unsafe_or_unknown_capture():
    for source in [SOURCE.replace('out = buffer','out = code'),
                   SOURCE.replace('out = buffer','out = acquire()'),
                   SOURCE.replace('base + offset','next()'),
                   SOURCE.replace('int status','unsigned int status'),
                   SOURCE.replace('int code;','int code; int *escape=&code;'),
                   SOURCE.replace('unsigned char *out;','volatile unsigned char *out;'),
                   SOURCE.replace('code = status;','code = status; use(status);')]:
        assert not prepared_call_repairs.variants(source,'f',arch='mips')
    assert not prepared_call_repairs.variants(SOURCE,'f','#define buffer (__LINE__)',arch='mips')
    assert not prepared_call_repairs.variants(SOURCE,'f','extern volatile unsigned char buffer[];',arch='mips')
    # Unrelated volatile hardware declarations must not disable the family globally.
    assert prepared_call_repairs.variants(SOURCE,'f','extern volatile int hardware;',arch='mips')


def test_rotation_detection_ranking_and_lane_use_existing_budget():
    d=allocation_evidence.diagnose(ROWS,'mips')
    assert d['return_copy_rotation']==['$a1','$a2']
    assert allocation_search.allowance(d)==allocation_search.CHAIN_LIMIT
    assert allocation_evidence.plan(ROWS,'mips')['families'][:2]==['prepared_return_scope','prepared_call_scope']
    assert repair_ranking.order(['call_one_shot_scope','prepared_return_scope','local_width'],ROWS)[0]=='prepared_return_scope'
    labels={}
    candidates=allocation_search.proposals(SOURCE,'f','mips',ROWS,[],labels)
    assert candidates and candidates[0][0]=='prepared_return_scope'
    assert 'raw linked bytes' in allocation_evidence.text(ROWS,'mips')
    assert not allocation_evidence.diagnose(ROWS,'arm')['return_copy_rotation']
    changed=[(m,a,b.replace('32','36')) for m,a,b in ROWS]
    assert not allocation_evidence.diagnose(changed,'mips')['return_copy_rotation']


def test_family_is_registered_in_shared_generation():
    labels={}
    candidates=draft_repairs.variants(SOURCE,'f','mips',labels=labels,limit=None)
    assert any(labels[c]=='prepared_return_scope' for c in candidates)
    assert set(prepared_call_repairs.FAMILIES)<=set(draft_repairs.AGENT_FAMILIES)


def test_large_donor_summary_is_bounded_and_preserves_interface_warning():
    donor='void g(void) { unsigned char *p; do { update(p); } while (0); goto loop; }'+' ' * 4000
    summary=donor_structure.brief(SOURCE,donor,.88)
    assert 'single-pass scopes: yours 0, donor 1' in summary
    assert 'argument/return types' in summary
    assert len(summary)<1100 and donor not in summary
    assert not donor_structure.brief(SOURCE,donor,.79)
    assert not donor_structure.brief(SOURCE,SOURCE,.99)


def test_diff_aligned_rotation_keeps_equal_address_context():
    rows=[('i','lui $s6, %hi(original)','lui $s6, %hi(unknown)'),
          ('o','move $a1, $v0',''),(' ','lui $a2, 0x8010','lui $a2, 0x8010'),
          (' ','addiu $a2, $a2, 32','addiu $a2, $a2, 32'),('o','','move $a1, $v0')]
    assert allocation_evidence.diagnose(rows,'mips')['return_copy_rotation']==['$a1','$a2']
    assert 'Other operand differences remain unresolved' in allocation_evidence.text(rows,'mips')


def test_search_packet_reads_cached_current_diagnostics_without_compiling(monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import (
        allocation_search,
        compiler_diagnostics,
        return_path_hypotheses,
        return_path_search,
        reviewed_search_seeds,
        search_brief,
        search_evidence,
    )

    fn={'name':'f','unit':'','id':2,'best_source':SOURCE,'best_score':80}
    store=SimpleNamespace(all=lambda *a:[])
    net=SimpleNamespace(project_row=lambda _:dict(id=1,arch='mips'),_function=lambda *a:fn,
                        _context=lambda *a:{'header':''},project_store=lambda _:store)
    def forbidden(*args,**kwargs):raise AssertionError('Reading a packet must not compile')
    monkeypatch.setattr(compiler_diagnostics,'diagnose',forbidden)
    monkeypatch.setattr(allocation_search,'capture',forbidden)
    monkeypatch.setattr(reviewed_search_seeds,'supported',lambda *a:True)
    monkeypatch.setattr(reviewed_search_seeds,'selected',lambda *a:[])
    monkeypatch.setattr(allocation_search,'saved',lambda *a:{'rows':ROWS,'allocation':{'values':[
        {'pseudo':74,'aliases':['a1'],'allocation_rank':1,'references':4,'live_insns':28,'source_lines':[7]}]}})
    monkeypatch.setattr(return_path_hypotheses,'brief',lambda *a:'')
    monkeypatch.setattr(return_path_search,'history',lambda *a:'')
    monkeypatch.setattr(search_evidence,'brief',lambda *a:'')
    packet=search_brief.read(net,None,'game','f')
    assert 'prepared_return_scope' in packet
    assert '4 refs/28 live insns' in packet
    assert 'get_candidate only when source is needed' in packet
    assert len(packet)<3000


def test_rotation_tries_cheap_lane_before_optional_capture(monkeypatch):
    from decompnet.app import deep_permuter
    from tests.test_deep_permuter import Log
    from tests.test_permute import FakeNet

    net=FakeNet();net.is_rom=lambda _:True
    search=deep_permuter.Search(net,1,20,Log(),net.function())
    monkeypatch.setattr(allocation_search,'saved',lambda *a:None)
    calls=[]
    def capture(*args,on_compile,rows=()):
        calls.append(1);on_compile();on_compile();return {}
    monkeypatch.setattr(allocation_search,'capture',capture)
    choices,lane=search.allocation_choices(net.project_row('game'),net.function(),SOURCE,ROWS,['broad'],{},'',None)
    assert len(lane)==1 and choices[-1]=='broad'
    assert not calls and search.tested==0
    search.allocation_trials=1
    search.local_seen.update(lane)  # the selected cheap proposal was actually judged
    search.allocation_choices(net.project_row('game'),net.function(),SOURCE,ROWS,['broad'],{},'',None)
    assert calls==[1] and search.tested==2 and search.allocation_diagnostic_compiles==2
    search.allocation_trials=allocation_search.CHAIN_LIMIT
    assert search.allocation_choices(net.project_row('game'),net.function(),SOURCE,ROWS,['broad'],{},'',None)==(['broad'],set())
