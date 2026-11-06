"""Free matching proposals are bounded work, never substitutes for the exact judge."""

from types import SimpleNamespace

import pytest

from decompnet.app import free_candidates as free
from decompnet.app import regional_layouts as layouts
from decompnet.app import similar_matches as sm
from decompnet.app.engine import RomNetwork
from decompnet.app.twins import Job
from decompnet.arch import mips_templates

from .test_rom import ROM, built  # noqa: F401
from .test_similar_matches import pair as pair_fixture

pair = pair_fixture


def fn(words, big=True, name='leaf'):
    return {'target': b''.join(w.to_bytes(4, 'big' if big else 'little') for w in words),
            'relocs': '[]', 'name': name, 'vram': 0x80001000}


@pytest.mark.parametrize('big', [True, False])
@pytest.mark.parametrize('word,fragment', [(0x24020007, '+ (7)'), (0x24820003, 'a + (3)'),
                                         (0x00851021, 'a + b'), (0x8c820010, '*(int *)'),
                                         (0xac850010, 'value;')])
def test_leaf_templates_cover_constants_arithmetic_getters_and_setters(big, word, fragment):
    candidates = mips_templates.candidates(fn([0x03e00008, word], big), big)
    assert len(candidates) == 1 and fragment in candidates[0]


def test_templates_reject_unmodelled_side_effects_and_allow_known_tail_wrappers():
    assert mips_templates.candidates(fn([0x03e00008, 0x24820001, 0xac850010]), True) == []
    assert mips_templates.candidates(fn([0x1000ffff, 0]), True) == []
    assert mips_templates.candidates(fn([0x08000800, 0]), True) == []
    assert 'return helper(a,b,c,d)' in mips_templates.candidates(fn([0x08000800, 0]), True, {0x80002000: 'helper'})[0]


def test_free_templates_only_count_judge_matches_and_cache_failures(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET target=?,size=8,n_insns=2 WHERE id=2", (fn([0x03e00008,0x24020007])['target'],))
    seen = []
    def judge(agent, proj, function, source):
        seen.append(source)
        return {'matched': False, 'score': 10}
    monkeypatch.setattr(pair, 'try_quietly', judge)
    assert free.run(pair.twins, 'destination', Job()) == 0
    assert free.run(pair.twins, 'destination', Job()) == 0
    assert len(seen) == 1
    assert pair._function('new', 'destination')['status'] == 'open'


def test_sdk_sources_are_local_explicit_and_compiler_scoped(pair, monkeypatch, tmp_path):
    proj = pair._project('destination')
    fingerprint = pair.twins.prints(proj)[2].key
    with pair.store.tx() as db:
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                   ('local SDK', 'mips', 1, 'helper', fingerprint, 32))
    path = tmp_path/'lib.c'
    path.write_text('int helper(int x) { return x+61; }\n')
    assert free.import_sources(pair, proj, 'local SDK', path) == 1
    seen = []
    def judge(agent, project, function, source):
        seen.append(source)
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?", (source,function['id']))
        return {'matched': True, 'score': 0}
    monkeypatch.setattr(pair, 'try_quietly', judge)
    job = Job()
    assert free.run(pair.twins, 'destination', job) == 1
    assert 'new(int x)' in seen[0] and job.reuse_bytes == 32
    assert pair.store.one('SELECT strategy FROM free_match_attempts')['strategy'] == 'library'


def test_sdk_import_rejects_ambiguous_definitions(pair, tmp_path):
    with pair.store.tx() as db:
        db.execute("INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES('SDK','mips',1,'f','x',32)")
    folder = tmp_path/'sdk'; folder.mkdir()
    (folder/'a.c').write_text('int f(void) { return 1; }')
    (folder/'b.c').write_text('int f(void) { return 2; }')
    assert free.import_sources(pair, pair._project('destination'), 'SDK', folder) == 0


def test_layout_learning_requires_two_independent_pairs_and_rejects_conflicts(pair):
    with pair.store.tx() as db:
        for a, b, old, new in [(3,4,16,20),(5,6,16,20)]:
            for fid, pid, offset in [(a,1,old),(b,2,new)]:
                db.execute("INSERT INTO functions(id,project_id,name,target,relocs,status,matched_source,vram,size,n_insns) VALUES(?,?,?,?,?,'matched','int x;',?,32,8)",
                           (fid,pid,f'f{fid}',fn([0x8c820000|offset,0x03e00008,0,0,0,0,0,0])['target'],'[]',0x80002000+fid*32))
            db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,created_at) VALUES(2,?,1,?,'match',0)", (b,a))
    mapping = layouts.learn(pair, pair._project('destination'), pair._project('source'))
    assert mapping == {(35,4,16):20}
    a, b = fn([0x8c820010,0x03e00008,0]), fn([0x8c820014,0x03e00008,0])
    assert layouts.variants('return *(int *)(p + 16); /* p + 16 */',a,b,mapping,True) == ['return *(int *)(p + 20); /* p + 16 */']
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET target=? WHERE id=6", (fn([0x8c820018,0x03e00008,0,0,0,0,0,0])['target'],))
    assert layouts.learn(pair, pair._project('destination'), pair._project('source')) == {}
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=6")
    assert layouts.learn(pair, pair._project('destination'), pair._project('source')) == {}


def test_operand_score_ten_is_eligible_and_feedback_survives_cache(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET matched_source=? WHERE id=1', ('int old(int x) {\n int y;\n y = x + 60;\n return y;\n}',))
    reports = []
    def attempt(agent, proj, a, b, source, feedback, **kwargs):
        feedback.update(percent=98.75, rows=[('i','addiu v0,a0,61','addiu v0,a0,60')])
        return 'miss', 'score 10'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    monkeypatch.setattr(sm, 'repair_variants', lambda source, feedback=None: reports.append(feedback) or [])
    monkeypatch.setattr(sm, 'deeper_variants', lambda *args: [])
    sm.run(pair.twins, 'destination', Job())
    sm.run(pair.twins, 'destination', Job())
    assert len(reports) == 2 and all(r['percent'] == 98.75 for r in reports)


def test_cascade_coalesces_notifications_and_stops_at_one_shared_budget(pair, monkeypatch):
    queued = []
    monkeypatch.setattr('decompnet.app.reuse_cascade.workers.pool', lambda: SimpleNamespace(submit=lambda f: queued.append(f)))
    pair.cascades.configure(True, attempts=1)
    donor = pair._project('source')
    pair.cascades.notify(donor,1); pair.cascades.notify(donor,1)
    assert len(queued) == 1
    def run(twins, slug, job):
        job.reuse_attempts += 1
        pair.cascades.notify(donor,1)
        return 0
    monkeypatch.setattr(pair.twins, 'across_games', lambda *args: 0)
    monkeypatch.setattr(free, 'run', run)
    monkeypatch.setattr(sm, 'run', lambda *args: 0)
    queued.pop()()
    assert not pair.cascades.running and not pair.cascades.pending and not queued
    pair.cascades.pause('destination')
    pair.cascades.notify(donor,1)
    assert not queued
    pair.cascades.resume('destination')
    pair.cascades.notify(donor,1)
    assert len(queued) == 1
    pair.cascades.configure(False)
    queued.pop()()
    assert not pair.cascades.running


def test_new_candidates_use_the_real_compiler_and_link_judge(built, tmp_path, monkeypatch):  # noqa: F811
    net = RomNetwork(tmp_path/'decomp.db',tmp_path)
    monkeypatch.setattr(net.twins,'after_match',lambda *args: None)
    slug = net.import_rom(ROM,confirmed=True,log=lambda *_: None)
    job = Job()
    assert free.run(net.twins,slug,job) > 0
    assert job.reuse_bytes > 0 and job.reuse_attempts <= job.reuse_limit


def test_sdk_c_matches_through_real_compiler_without_a_donor_project(built, tmp_path, monkeypatch):  # noqa: F811
    from pathlib import Path
    net = RomNetwork(tmp_path/'decomp.db',tmp_path)
    monkeypatch.setattr(net.twins,'after_match',lambda *args: None)
    slug = net.import_rom(ROM,confirmed=True,log=lambda *_: None)
    demo = Path(__file__).parents[1]/'projects/demo-rom'
    assert net.twins.import_sdk_objects(demo/'build/libc.o','homemade libc') > 0
    header = tmp_path/'types.h'; header.write_text('typedef unsigned int size_t;\n')
    assert free.import_sources(net,net._project(slug),'homemade libc',demo/'src/libc.c',header) > 0
    job = Job()
    free.run(net.twins,slug,job)
    assert net.store.one("SELECT 1 FROM free_match_attempts WHERE strategy='library' AND outcome='match'")


def test_exact_cascade_attempts_honour_shared_budget(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET target=(SELECT target FROM functions WHERE id=1) WHERE id=2')
    seen = []
    monkeypatch.setattr(pair.twins,'_attempt_twin',lambda *a,**k: (seen.append(1) or 'miss','score 20'))
    job = Job(); free.budget(job,attempts=0)
    assert pair.twins.across_games('destination',job) == 0 and not seen
    job = Job(); free.budget(job,attempts=1)
    pair.twins.across_games('destination',job)
    assert len(seen) == 1 and job.reuse_attempts == 1


def test_claimed_templates_do_not_compile_or_consume_budget(pair, monkeypatch):
    agent, _ = pair.local_agent('worker')
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET target=?,size=8,n_insns=2 WHERE id=2", (fn([0x03e00008,0x24020007])['target'],))
        db.execute('INSERT INTO claims VALUES(?,?,0,9999999999)',(2,agent.id))
    monkeypatch.setattr(pair,'try_quietly',lambda *a: pytest.fail('claimed function was compiled'))
    job = Job()
    assert free.run(pair.twins,'destination',job) == 0 and job.reuse_attempts == 0


def test_diff_register_markers_select_register_repairs(monkeypatch):
    seen = []
    def operation(lines, rng):
        seen.append(True)
        return [*lines, '/* unchanged */']
    for name in ('_shuffle_declarations', '_toggle_register', '_retype_local'):
        monkeypatch.setattr(sm.permuter, name, operation)
    monkeypatch.setattr(sm.permuter, 'mutate', lambda *a: pytest.fail('unguided mutation used'))
    # Protected comments are unchanged only when the operation preserves them; these proposals are discarded.
    assert sm.repair_variants('int f(void) { return 1; }', {'rows': [('r', 'a0', 'a1')]}) == []
    assert seen


def test_sdk_source_import_does_not_cross_compiler_settings(pair, monkeypatch, tmp_path):
    proj = pair._project('destination')
    fingerprint = pair.twins.prints(proj)[2].key
    with pair.store.tx() as db:
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                   ('SDK', 'mips', 1, 'helper', fingerprint, 32))
    source = tmp_path/'lib.c'; source.write_text('int helper(int x) { return x; }')
    free.import_sources(pair, proj, 'SDK', source)
    with pair.store.tx() as db:
        db.execute("UPDATE sdk_sources SET compiler='different compiler'")
    monkeypatch.setattr(pair, 'try_quietly', lambda *a: pytest.fail('wrong compiler corpus used'))
    assert free.run(pair.twins, 'destination', Job()) == 0


def test_saved_near_miss_address_repairs_use_cached_probe_and_byte_judge(pair, monkeypatch):
    from .test_similar_matches import code
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int new(int x) { return original + 60; }',best_score=20 WHERE id=2")
        db.execute("INSERT INTO symbols(project_id,name,address) VALUES(2,'destination',2147487744)")
    seen = []
    def judge(agent, proj, function, source):
        seen.append(source)
        return {'matched': False, 'score': 20, 'candidate_target': code(60).hex(), 'candidate_relocs': '[]',
                'rows': [('i','lui $v0, %hi(destination)','lui $v0, %hi(original)'),
                         ('i','lw $v0, %lo(destination)($v0)','lw $v0, %lo(original)($v0)')]}
    monkeypatch.setattr(pair, 'try_quietly', judge)
    assert free.run(pair.twins,'destination',Job()) == 0
    assert any('destination + 61' in s for s in seen)
    assert pair._function('new','destination')['status'] == 'open'
    count = len(seen)
    assert free.run(pair.twins,'destination',Job()) == 0
    assert len(seen) == count


def test_sdk_symbol_map_rejects_small_aliases_and_multiple_destinations():
    signatures = [
        {'name': 'getter', 'fingerprint': 'tiny', 'size': 8},
        {'name': 'first', 'fingerprint': 'alias', 'size': 64},
        {'name': 'second', 'fingerprint': 'alias', 'size': 64},
        {'name': 'shared', 'fingerprint': 'duplicate', 'size': 64},
        {'name': 'unique', 'fingerprint': 'exact', 'size': 32},
    ]
    targets = {'tiny': {'a'}, 'alias': {'b'}, 'duplicate': {'c', 'd'}, 'exact': {'e'}}
    assert free.sdk_symbol_map(signatures, targets) == {'unique': 'e'}


def test_sdk_symbol_map_rejects_one_name_with_conflicting_variants():
    signatures = [{'name': 'helper', 'fingerprint': key, 'size': 32} for key in ('one', 'two')]
    assert free.sdk_symbol_map(signatures, {'one': {'a'}, 'two': {'b'}}) == {}
    assert free.sdk_symbol_map(signatures, {'one': {'a'}, 'two': {'a'}}) == {'helper': 'a'}


def test_sdk_symbol_map_rejects_alias_even_with_another_unique_variant():
    signatures = [
        {'name': 'helper', 'fingerprint': 'one', 'size': 32},
        {'name': 'helper', 'fingerprint': 'two', 'size': 32},
        {'name': 'alias', 'fingerprint': 'two', 'size': 32},
    ]
    assert free.sdk_symbol_map(signatures, {'one': {'a'}, 'two': {'a'}}) == {}


def test_free_candidate_capture_identifies_its_automatic_origin_without_reviewing_it(pair,monkeypatch):
    from decompnet.repair_feedback import _TRIAL
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET target=?,size=8,n_insns=2 WHERE id=2',(fn([0x03e00008,0x24020007])['target'],))
    monkeypatch.setattr(pair,'on_match',lambda *args:None)
    def judge(agent,proj,function,source):
        pair._record(function,agent,0,source,1,matched=True)
        return {'matched':True,'score':0}
    monkeypatch.setattr(pair,'try_quietly',judge)
    assert free.run(pair.twins,'destination',Job())==1
    case=pair.repair_feedback.listing('destination')['examples'][0]['id']
    detail=pair.repair_feedback.detail('destination',case)
    assert detail['evidence']['provenance']=={'kind':'automatic candidate','family':'template','origin':'MIPS leaf template'}
    assert detail['status']=='new' and _TRIAL.get() is None


def test_cached_discovery_yields_time_and_resumes_after_last_visited_function(pair, monkeypatch):
    import threading

    from decompnet.app import free_candidates
    clock = [0.0]
    monkeypatch.setattr(free_candidates, 'time', SimpleNamespace(monotonic=lambda:clock[0], time=lambda:0))
    target = fn([0x03e00008,0x24020007])['target']
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET target=?,size=8,n_insns=2 WHERE id=2', (target,))
        db.execute("INSERT INTO functions(id,project_id,name,target,size,n_insns,status,vram,relocs) "
                   "SELECT 3,project_id,'next',target,size,n_insns,'open',vram+16,relocs FROM functions WHERE id=2")
    visits = []
    def cached_templates(function, *args):
        visits.append(function['id'])
        clock[0] += 6  # Discovery, not compilation, consumes the phase's share of ten seconds.
        return []
    monkeypatch.setattr(free_candidates.mips_templates, 'candidates', cached_templates)
    for _ in range(2):
        job = Job(stop=threading.Event())
        free_candidates.budget(job, seconds=10, attempts=8)
        free_candidates.run(pair.twins, 'destination', job)
        assert job.reuse_attempts == 0
        assert clock[0] < job.reuse_deadline  # Wider discovery still gets time.
    assert visits == [2, 3]


def test_typed_sdk_name_can_supply_a_candidate_when_flags_changed_its_fingerprint(pair, monkeypatch, tmp_path):
    proj = pair._project('destination')
    with pair.store.tx() as db:
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                   ('typed SDK','mips',1,'new','another compiler shape',32))
    source=tmp_path/'typed.c'
    source.write_text('int new(int x) { return x+61; }')
    header=tmp_path/'typed.h'
    header.write_text('typedef struct { int value; } TypedPayload;')
    assert free.import_sources(pair,proj,'typed SDK',source,header) == 1
    seen=[]
    def judge(agent, project, function, candidate):
        seen.append(candidate)
        return {'matched':False,'score':20,'rows':[]}
    monkeypatch.setattr(pair,'try_quietly',judge)
    assert free.run(pair.twins,'destination',Job()) == 0
    assert seen and 'TypedPayload' in seen[0] and 'new(int x)' in seen[0]


def test_wrapper_candidates_reach_the_judge_and_failed_trials_are_cached(pair,monkeypatch):
    from .test_wrapper_families import fn as wrapper
    donor=wrapper(0x123400,'old');target=wrapper(0x125690,'new',2)
    source='extern int D_123400; void helper(int,int,int*); int old(int a,int b,int c){helper(c,b,&D_123400);return 7;}'
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET project_id=2,target=?,relocs=?,size=44,n_insns=11,matched_source=? WHERE id=1',
                   (donor['target'],donor['relocs'],source))
        db.execute('UPDATE functions SET target=?,relocs=?,size=44,n_insns=11 WHERE id=2',
                   (target['target'],target['relocs']))
        # The real callee identity is independent of the global address substitution.
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(2,'helper',?,4,'func')",(0x80008000,))
    seen=[]
    def judge(agent,proj,function,source):
        seen.append(source)
        assert 'D_00125690' in source and 'int new(' in source
        return {'matched':False,'score':10}
    monkeypatch.setattr(pair,'try_quietly',judge)
    assert free.run(pair.twins,'destination',Job())==0
    assert free.run(pair.twins,'destination',Job())==0
    assert len(seen)==1 and pair._function('new','destination')['status']=='open'
    assert pair.store.one('SELECT strategy FROM free_match_attempts')['strategy']=='wrapper-family'
