"""Similarity can propose C without tokens, but only the ordinary judge can accept it."""

import json

import pytest

from decompnet.app import similar_matches as sm
from decompnet.app.engine import RomNetwork
from decompnet.app.twins import Job

from .test_app_engine import DAMAGE
from .test_rom import ROM, built  # noqa: F401
from .test_twins_across_games import copy_project


def code(value, big=True):
    return b"".join(w.to_bytes(4, "big" if big else "little") for w in
                    [0x24820000 | value, 0x24420002, 0x24420003, 0x24420004,
                     0x24420005, 0x24420006, 0x03e00008, 0])


@pytest.mark.parametrize("big", [True, False])
def test_literal_changes_leave_comments_strings_and_symbol_names_alone(big):
    source = 'int func_60(int x) { /* 60 */ char *s = "60"; return x + 60U; }'
    a, b = {"target": code(60, big), "relocs": "[]"}, {"target": code(61, big), "relocs": "[]"}
    assert sm.literal_variants(source, a, b, big) == [source.replace('x + 60U', 'x + 61U')]
    assert sm.literal_variants('return 60 + 60;', a, b, big) == []
    assert sm.literal_variants('return -60;', a, b, big) == []
    a['relocs'] = b['relocs'] = '[[0,5,2147483648]]'
    assert sm.literal_variants(source, a, b, big) == []


def test_changed_registers_or_instructions_do_not_propose_literal_edits():
    a = {"target": code(60), "relocs": "[]"}
    b = {"target": (0x24a2003d).to_bytes(4, 'big') + code(61)[4:], "relocs": "[]"}
    assert sm.literal_variants('return x + 60;', a, b, True) == []


@pytest.fixture
def pair(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    cfg = {'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']},
           'big_endian': True, 'rom': {'sha1': 'test'}}
    with net.store.tx() as db:
        for pid, slug in [(1, 'source'), (2, 'destination')]:
            db.execute('INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)',
                       (pid, slug, slug, 'mips', 1, str(tmp_path / slug), json.dumps(cfg)))
            db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,?,?,?)',
                       (pid, 1, '', ''))
            db.execute('INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status,matched_source) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (pid, pid, 'old' if pid == 1 else 'new', 32, 8, code(60 if pid == 1 else 61), '[]',
                        0x80001000, '', 'matched' if pid == 1 else 'open',
                        'int old(int x) { return x + 60; }' if pid == 1 else None))
    monkeypatch.setattr(net, 'is_rom', lambda p: True)
    monkeypatch.setattr(net, 'fork_slug', lambda s: None)
    return net


def test_similar_candidates_are_judged_and_recorded_and_misses_are_cached(pair, monkeypatch):
    seen = []
    def attempt(agent, proj, a, b, source, **kwargs):
        seen.append(source)
        assert kwargs['similar'] is True
        return 'miss', 'score 20'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    job = Job()
    assert sm.run(pair.twins, 'destination', job) == 0
    assert 2 <= len(seen) <= 10 and 'new(int' in seen[0] and '+ 61' in seen[1]
    count = len(seen)
    assert pair._function('new', 'destination')['status'] == 'open'
    assert pair.twins.cross_game_results('destination')['counts'] == {'miss': count}
    assert sm.run(pair.twins, 'destination', Job()) == 0
    assert len(seen) == count


def test_only_judge_acceptance_counts_and_stops_further_candidates(pair, monkeypatch):
    def attempt(agent, proj, a, b, source, **kwargs):
        if '+ 61' not in source:
            return 'miss', 'score 20'
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?", (source, b['id']))
        return 'match', ''
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    job = Job()
    assert sm.run(pair.twins, 'destination', job) == 1
    assert job.cross_matched == 1 and job.cross_tried == 2
    assert sm.run(pair.twins, 'destination', Job()) == 0


@pytest.mark.parametrize('blocked', ['claimed', 'compiler', 'relocations', 'cancelled', 'budget', 'time', 'fork'])
def test_no_attempt_when_incompatible_busy_cancelled_or_over_budget(pair, monkeypatch, blocked):
    job = Job()
    if blocked == 'claimed':
        agent, _ = pair.local_agent('worker')
        with pair.store.tx() as db:
            db.execute('INSERT INTO claims VALUES(?,?,?,?)', (2, agent.id, 0, 9999999999))
    elif blocked == 'compiler':
        p = pair._project('destination'); cfg = json.loads(p['config']); cfg['compiler']['toolchain'] = 'other-compiler'
        with pair.store.tx() as db:
            db.execute('UPDATE projects SET config=? WHERE id=2', (json.dumps(cfg),))
    elif blocked == 'relocations':
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET relocs='[[0,5,2147487744]]' WHERE id=2")
    elif blocked == 'cancelled':
        job.stop.set()
    elif blocked == 'budget':
        monkeypatch.setattr(sm, 'MAX_ATTEMPTS', 0)
    elif blocked == 'time':
        monkeypatch.setattr(sm, 'MAX_SECONDS', 0)
    elif blocked == 'fork':
        monkeypatch.setattr(pair, 'fork_slug', lambda s: s)
    def forbidden(*args, **kwargs):
        pytest.fail('must not compile this candidate')
    monkeypatch.setattr(pair.twins, '_attempt_twin', forbidden)
    assert sm.run(pair.twins, 'destination', job) == 0


def test_a_regional_constant_change_matches_with_the_real_compiler_and_link_judge(built, tmp_path, monkeypatch):  # noqa: F811
    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    monkeypatch.setattr(net.twins, 'after_match', lambda *args: None)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('tester')
    assert net.compile_and_diff(agent, 'func_80000808', DAMAGE, slug).startswith('✅ MATCH')
    pid = copy_project(net, slug, 'another-region')
    fn = net._function('func_80000808', 'another-region')
    target = bytearray(fn['target'])
    offsets = [i for i in range(0, len(target), 4)
               if int.from_bytes(target[i:i+4], 'big') & 0xffff == 60
               and int.from_bytes(target[i:i+4], 'big') >> 26 == 9]
    assert offsets
    for offset in offsets:  # the compiler duplicated the assignment across two branches
        target[offset + 3] = 61
    with net.store.tx() as db:
        db.execute('UPDATE functions SET target=? WHERE id=?', (bytes(target), fn['id']))
        db.execute("UPDATE functions SET status='asm' WHERE project_id=? AND id!=?", (pid, fn['id']))
    assert net.twins.across_games('another-region') == 0
    assert sm.run(net.twins, 'another-region', Job()) == 1
    result = net._function(fn['name'], 'another-region')
    assert result['status'] == 'matched' and '= 61;' in result['matched_source']
    assert 'adapted from similar verified C' in result['matched_source']


def test_budget_leaves_work_for_next_pass_and_cancellation_stops_between_attempts(pair, monkeypatch):
    seen = []
    job = Job()
    def attempt(agent, proj, a, b, source, **kwargs):
        seen.append(source)
        job.stop.set()
        return 'miss', 'score 20'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    assert sm.run(pair.twins, 'destination', job) == 0
    assert len(seen) == 1
    job = Job()
    monkeypatch.setattr(sm, 'MAX_ATTEMPTS', 1)
    assert sm.run(pair.twins, 'destination', job) == 0
    assert len(seen) == 2 and '+ 61' in seen[-1]


def test_full_pass_runs_similarity_after_exact_transfers(pair, monkeypatch):
    order = []
    monkeypatch.setattr(pair.twins, 'set_aside_asm', lambda p: 0)
    monkeypatch.setattr(pair.twins, 'update_boosts', lambda *a, **k: None)
    monkeypatch.setattr(pair.twins, 'run', lambda *a: order.append('local'))
    monkeypatch.setattr(pair.twins, 'across_games', lambda *a: order.append('exact') or 0)
    def similar(twins, slug, job):
        assert job.state == 'running'
        order.append('similar')
        return 2
    monkeypatch.setattr(sm, 'run', similar)
    result = pair.twins.full_pass('destination')
    assert order == ['local', 'exact', 'similar'] and result.matched == 2 and result.state == 'done'


def test_near_miss_repairs_are_bounded_deterministic_and_judged(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE id=1",
                   ('int old(int x) {\n    int y;\n    y = x + 60;\n    return y;\n}',))
    seen = []
    def attempt(agent, proj, a, b, source, **kwargs):
        seen.append(source)
        return 'miss', 'score 1'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    sm.run(pair.twins, 'destination', Job())
    assert 2 < len(seen) <= 10
    first = list(seen)
    sm.run(pair.twins, 'destination', Job())
    assert seen == first
    with pair.store.tx() as db:
        db.execute("UPDATE context_versions SET header='typedef int s32;' WHERE project_id=2")
    sm.run(pair.twins, 'destination', Job())
    assert len(seen) > len(first)
    assert pair._function('new', 'destination')['status'] == 'open'


def test_symbol_changes_invalidate_cached_compiles(pair, monkeypatch):
    seen = []
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda *args, **kwargs: (seen.append(1) or 'miss', 'score 20'))
    sm.run(pair.twins, 'destination', Job())
    sm.run(pair.twins, 'destination', Job())
    count = len(seen)
    assert 2 <= count <= 10
    with pair.store.tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address) VALUES(2,'global',1234)")
    sm.run(pair.twins, 'destination', Job())
    assert len(seen) == count * 2


def test_resume_cursor_survives_restart_and_avoids_starving_later_functions(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status) "
                   "SELECT 3,project_id,'later',size,n_insns,target,relocs,vram+32,unit,status FROM functions WHERE id=2")
    monkeypatch.setattr(sm, 'MAX_ATTEMPTS', 1)
    seen = []
    def attempt(agent, proj, a, b, source, **kwargs):
        seen.append(b['id'])
        return 'miss', 'score 20'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    for _ in range(4):
        sm.run(pair.twins, 'destination', Job())
    assert seen == [2, 2, 3, 3]
    assert pair.store.one('SELECT function_id FROM reuse_cursors WHERE project_id=2')['function_id'] == 3


def test_regional_map_uses_only_current_verified_unambiguous_pairs(pair):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='int new(int x) { return x+61; }' WHERE id=2")
        db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,created_at) "
                   "VALUES(2,2,1,1,'match',0)")
    assert sm.regional_names(pair.twins, pair._project('destination'), pair._project('source')) == {'old': 'new'}
    with pair.store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status,matched_source) "
                   "SELECT 3,project_id,'conflict',size,n_insns,target,relocs,vram+32,unit,status,matched_source FROM functions WHERE id=2")
        db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,created_at) "
                   "VALUES(2,3,1,1,'match',0)")
    assert sm.regional_names(pair.twins, pair._project('destination'), pair._project('source')) == {}
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=3")
    assert sm.regional_names(pair.twins, pair._project('destination'), pair._project('source')) == {'old': 'new'}


def test_donor_flags_are_trials_not_silent_configuration_changes(pair, monkeypatch):
    cfg = json.loads(pair._project('destination')['config'])
    cfg['compiler']['flags'] = ['-O0']
    with pair.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=2', (json.dumps(cfg),))
    monkeypatch.setattr(sm.toolchains, 'installed', lambda tc: True)
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda *args, **kwargs: ('miss', 'score 20'))
    seen = []
    def judge(proj, fn, tc, flags, source):
        assert pair.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id'])
        seen.append((tc, flags))
        return True, None, ''
    monkeypatch.setattr(pair, '_judge_with', judge)
    job = Job()
    assert sm.run(pair.twins, 'destination', job) == 0
    assert seen == [('gcc-2.7.2-kmc', ['-O2'])]
    assert job.cross_matched == 0 and 3 <= job.cross_tried <= 11
    assert pair.cfg(pair._project('destination'))['compiler']['flags'] == ['-O0']
    assert pair.store.one('SELECT text FROM notes WHERE function_id=2')['text'].startswith('byte match with')
    assert not pair.store.one('SELECT 1 FROM claims WHERE function_id=2')
    sm.run(pair.twins, 'destination', Job())
    assert len(seen) == 1


def test_compiler_trial_releases_claim_on_failure_and_never_installs(pair, monkeypatch):
    def failed(*args):
        raise sm.toolchains.ToolchainError('missing runtime')
    monkeypatch.setattr(pair, '_judge_with', failed)
    agent, _ = pair.local_agent('autopilot')
    outcome, _ = sm.compiler_trial(pair, agent, pair._project('destination'), pair._function('new', 'destination'),
                                   'int new(void) { return 1; }', {'toolchain': 'gcc-2.7.2-kmc'})
    assert outcome == 'skip'
    assert not pair.store.one('SELECT 1 FROM claims WHERE function_id=2')


def test_explicit_memory_offsets_can_be_repaired_but_not_registers():
    a = {'target': (0x8c820010).to_bytes(4, 'big') + code(60)[4:], 'relocs': '[]'}
    b = {'target': (0x8c820014).to_bytes(4, 'big') + code(60)[4:], 'relocs': '[]'}
    assert sm.literal_variants('return *(int *)(p + 16);', a, b, True) == ['return *(int *)(p + 20);']
    b['target'] = (0x8ca20014).to_bytes(4, 'big') + code(60)[4:]
    assert sm.literal_variants('return *(int *)(p + 16);', a, b, True) == []


def test_learned_names_enable_candidates_with_different_relocation_layouts(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int old(int x) { return donor_helper(x)+60; }', "
                   "relocs='[[0,5,2147491840]]' WHERE id=1")
        for fid, pid, name in [(3, 1, 'donor_helper'), (4, 2, 'recipient_helper')]:
            db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status,matched_source) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (fid,pid,name,32,8,code(90),'[]',0x80002000,'','matched',
                                                        'int '+name+'(int x) { return x+90; }'))
        db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,created_at) "
                   "VALUES(2,4,1,3,'match',0)")
    seen = []
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda agent, proj, a, b, source, **kw:
                        (seen.append(source) or 'miss', 'score 20'))
    sm.run(pair.twins, 'destination', Job())
    assert any('recipient_helper(x)' in source for source in seen)
    assert all('donor_helper(x)' not in source for source in seen)
    assert pair._function('new', 'destination')['status'] == 'open'


def test_reopened_function_is_rejudged_even_with_a_cached_acceptance(pair, monkeypatch):
    seen = []
    def attempt(agent, proj, a, b, source, **kwargs):
        seen.append(source)
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?", (source,b['id']))
        return 'match', ''
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    assert sm.run(pair.twins, 'destination', Job()) == 1
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=2")
    assert sm.run(pair.twins, 'destination', Job()) == 1
    assert len(seen) == 2


def test_repairs_wait_until_easy_transfers_have_been_tried(pair, monkeypatch):
    source = 'int old(int x) {\n    int y;\n    y = x + 60;\n    return y;\n}'
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET matched_source=? WHERE id=1', (source,))
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status) "
                   "SELECT 3,project_id,'later',size,n_insns,target,relocs,vram+32,unit,status FROM functions WHERE id=2")
    seen = []
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda agent, proj, a, b, source, **kw:
                        (seen.append((b['id'],source)) or 'miss', 'score 1'))
    sm.run(pair.twins, 'destination', Job())
    assert [fid for fid, _ in seen[:4]] == [2, 2, 3, 3]
    assert len(seen) > 4


def test_trial_cache_and_resume_work_with_a_new_network_instance(pair, monkeypatch, tmp_path):
    monkeypatch.setattr(sm, 'MAX_ATTEMPTS', 1)
    seen = []
    def attempt(agent, proj, a, b, source, **kwargs):
        seen.append(source)
        return 'miss', 'score 20'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    sm.run(pair.twins, 'destination', Job())
    restarted = RomNetwork(pair.store.path, tmp_path)
    monkeypatch.setattr(restarted, 'is_rom', lambda p: True)
    monkeypatch.setattr(restarted, 'fork_slug', lambda s: None)
    monkeypatch.setattr(restarted.twins, '_attempt_twin', attempt)
    sm.run(restarted.twins, 'destination', Job())
    assert len(seen) == 2 and '+ 61' in seen[1]


def test_repair_generator_preserves_string_and_comment_contents(monkeypatch):
    source = 'int f(void) {\n    char *s = "(int)x"; /* x++ */\n    return 0;\n}'
    monkeypatch.setattr(sm.permuter, 'mutate', lambda text, rng: text.replace('(int)x', 'x'))
    assert sm.repair_variants(source) == []


def test_large_transfer_queue_reserves_attempts_for_repairs_and_keeps_cursor(pair, monkeypatch):
    from decompnet.app.free_candidates import budget
    with pair.store.tx() as db:
        for fid in (3, 4, 5, 6):
            db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status) "
                       "SELECT ?,project_id,?,size,n_insns,target,relocs,vram+?,unit,status FROM functions WHERE id=2",
                       (fid, f'next_{fid}', fid * 32))
    seen = []
    def attempt(agent, proj, a, b, source, feedback, **kwargs):
        repaired = '/* repair */' in source
        seen.append((b['id'], repaired))
        if repaired:
            with pair.store.tx() as db:
                db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?", (source,b['id']))
            return 'match', ''
        feedback.update(percent=98.75, rows=[('i', '61', '60')])
        return 'miss', 'score 10'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    monkeypatch.setattr(sm, 'literal_variants', lambda *a: [])
    monkeypatch.setattr(sm, 'repair_variants', lambda source, report: [source + ' /* repair */'])
    monkeypatch.setattr(sm, 'deeper_variants', lambda *a: [])
    job = Job(); budget(job, attempts=4)
    assert sm.run(pair.twins, 'destination', job) == 1
    assert [repair for _, repair in seen] == [False, False, False, True]
    assert job.reuse_attempts == 4
    cursor = pair.store.one('SELECT function_id FROM reuse_cursors WHERE project_id=2')['function_id']
    assert cursor > 0
    seen.clear()
    job = Job(); budget(job, attempts=4)
    sm.run(pair.twins, 'destination', job)
    assert any(fid > cursor for fid, _ in seen)


def test_address_and_literal_repairs_reach_judge_as_one_candidate(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int old(int x) { return original + 60; }' WHERE id=1")
        db.execute("INSERT INTO symbols(project_id,name,address) VALUES(2,'destination',2147487744)")
    seen = []
    def attempt(agent, proj, a, b, source, feedback, **kwargs):
        seen.append(source)
        if 'destination + 61' in source:
            with pair.store.tx() as db:
                db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?", (source,b['id']))
            return 'match', ''
        feedback.update(percent=97.5, candidate_target=code(60).hex(), candidate_relocs='[]',
                        rows=[('i','lui $v0, %hi(destination)','lui $v0, %hi(original)'),
                              ('i','lw $v0, %lo(destination)($v0)','lw $v0, %lo(original)($v0)')])
        return 'miss', 'score 20'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    monkeypatch.setattr(sm, 'deeper_variants', lambda *a: [])
    assert sm.run(pair.twins, 'destination', Job()) == 1
    assert any('destination + 61' in s for s in seen)
    assert pair.store.one("SELECT 1 FROM cross_game_attempts WHERE outcome='match' AND reason LIKE '%; repair;%'")


@pytest.mark.parametrize('big', [True, False])
@pytest.mark.parametrize('declaration', ['extern char **text;', 'extern int text[];', 'extern uint32_t *text;'])
def test_table_id_macro_uses_word_offset_evidence(big, declaration):
    source = '#define MESSAGE 245U\n' + declaration + '\nvoid *f(void) { return text[MESSAGE]; }\n'
    order = 'big' if big else 'little'
    a = {'target': (0x8c820000 | 980).to_bytes(4, order), 'relocs': '[]'}
    b = {'target': (0x8c820000 | 960).to_bytes(4, order), 'relocs': '[]'}
    assert sm.literal_variants(source, a, b, big) == [source.replace('245U', '240U')]
    a['relocs'] = '[[0,6,0]]'
    assert sm.literal_variants(source, a, b, big) == []


@pytest.mark.parametrize('source', [
    '#define ID 245\nextern char *text;\nint f(void){return text[ID];}',
    '#define ID 245\nextern short text[];\nint f(void){return text[ID];}',
    '#define ID 245\n#define OTHER 245\nextern int text[];\nint f(void){return text[ID];}',
    '#define ID 245\nextern int text[];\nint f(void){return text[ID]+ID;}',
    '#define ID 245\n#define ID 246\nextern int text[];\nint f(void){return text[ID];}',
    '#define ID(x) 245\nextern int text[];\nint f(void){return text[ID(0)];}',
    '#define ID 245\nextern int text[];\nchar *s="text[ID]";\nint f(void){return ID;}',
])
def test_table_id_macro_skips_ambiguous_types_definitions_and_uses(source):
    a = {'target': (0x8c820000 | 980).to_bytes(4, 'big'), 'relocs': '[]'}
    b = {'target': (0x8c820000 | 960).to_bytes(4, 'big'), 'relocs': '[]'}
    assert sm.literal_variants(source, a, b, True) == []


def test_table_id_macro_does_not_scale_arithmetic_immediates():
    source = '#define ID 245\nextern int text[];\nint f(void){return text[ID];}'
    a = {'target': (0x24820000 | 980).to_bytes(4, 'big'), 'relocs': '[]'}
    b = {'target': (0x24820000 | 960).to_bytes(4, 'big'), 'relocs': '[]'}
    assert sm.literal_variants(source, a, b, True) == []


def test_successful_transfers_remain_available_after_many_recent_misses(pair):
    with pair.store.tx() as db:
        for index in range(36):
            db.execute('INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,reason,created_at) VALUES(2,2,1,1,?,?,?)',
                       ('match' if index == 0 else 'miss', 'score 0' if index == 0 else 'score 430', index))
    result = pair.twins.cross_game_results('destination')
    assert all(r['outcome'] == 'miss' for r in result['recent'])
    assert len(result['matches']) == 1 and result['matches'][0]['outcome'] == 'match'
    assert result['matches'][0]['source_slug'] == 'source'
    assert result['recent'][0]['source_slug'] == 'source'
