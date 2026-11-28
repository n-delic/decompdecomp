"""Similarity can propose C without tokens, but only the ordinary judge can accept it."""

import json

import pytest

from decompnet.app import similar_matches as sm
from decompnet.app.engine import RomNetwork
from decompnet.app.twins import Job

from .test_app_engine import DAMAGE
from .test_rom import ROM, built  # noqa: F401
from .test_twins_across_games import copy_project


@pytest.mark.parametrize('gate', ['missing fingerprint', 'small', 'assembly', 'claimed', 'cancelled'])
def test_rejected_targets_never_decode_their_candidate_payload(pair, monkeypatch, gate):
    from types import SimpleNamespace

    from decompnet import large_text_storage, payload_storage

    large_text_storage.install(pair.store.conn, {'functions'})
    source = '/* '+('synthetic unused source\n'*4000)+' */\nint ignored(void){return 0;}'
    with pair.store.tx() as db:
        db.execute('INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status,best_source) '
                   "VALUES(100,2,'ignored',32,8,?,'[]',2147491840,'','open',?)", (code(61), source))
    assert pair.store.one('SELECT typeof(best_source) AS storage FROM main.functions WHERE id=100')['storage'] == 'blob'
    if gate == 'claimed':
        agent, _ = pair.local_agent('busy')
        with pair.store.tx() as db:
            db.execute('INSERT INTO claims(function_id,agent_id,claimed_at,expires_at) VALUES(100,?,0,9999999999)', (agent.id,))
    original_prints = pair.twins.prints
    def prints(proj):
        result = dict(original_prints(proj))
        if proj['id'] == 2:
            result.pop(100, None)
            if gate != 'missing fingerprint':
                result[100] = SimpleNamespace(size=8 if gate == 'small' else 32, asm=gate == 'assembly')
        return result
    monkeypatch.setattr(pair.twins, 'prints', prints)
    calls = []
    digest = payload_storage.digest(source)
    def unpack(key, payload):
        if key == digest:calls.append(key)
        return payload_storage.unpack(key, payload)
    pair.store.conn.create_function('payload_read', 2, unpack, deterministic=True)
    pair.store._reader().create_function('payload_read', 2, unpack, deterministic=True)
    assert pair.store.one('SELECT best_source FROM functions WHERE id=100')['best_source'] == source
    assert calls == [digest]
    calls.clear()
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda *a, **kw: ('miss', 'score 20'))
    job = Job()
    if gate == 'cancelled':
        monkeypatch.setattr('decompnet.app.cross_game_progress.report', lambda *a, **kw: job.stop.set())
    sm.run(pair.twins, 'destination', job)
    assert calls == []


@pytest.mark.parametrize('change', ['matched', 'deleted'])
def test_target_details_are_rechecked_after_the_queue_snapshot(pair, monkeypatch, change):
    read = pair.store.one
    changed = []
    def one(sql, *args):
        row = read(sql, *args)
        if sql == 'SELECT 1 FROM claims WHERE function_id=?' and args == (2,) and not changed:
            changed.append(True)
            with pair.store.tx() as db:
                if change == 'deleted':db.execute('DELETE FROM functions WHERE id=2')
                else:db.execute("UPDATE functions SET status='matched' WHERE id=2")
        return row
    monkeypatch.setattr(pair.store, 'one', one)
    attempts = []
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda *a, **kw: (attempts.append(True) or 'miss', 'score 20'))
    sm.run(pair.twins, 'destination', Job())
    assert changed and not attempts


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


def test_manual_search_tries_wider_donor_with_shifted_references(pair, monkeypatch):
    new_target = bytes.fromhex('3c03123434630001') + code(61)
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET target=?,size=40,n_insns=10,relocs='[[8,4,2147491840]]' WHERE id=2",
                   (new_target,))
        db.execute("UPDATE functions SET relocs='[[0,4,2147495936]]', "
                   "matched_source='int old(int x) { return donor_callee(x); }' WHERE id=1")
        db.execute("INSERT INTO symbols(project_id,name,address,kind) VALUES(1,'donor_callee',2147495936,'function')")
        db.execute("INSERT INTO symbols(project_id,name,address,kind) VALUES(2,'target_callee',2147491840,'function')")
    seen = []

    def attempt(agent, proj, donor, target, source, **kwargs):
        seen.append(source)
        assert 'return target_callee(x);' in source
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=2", (source,))
        return 'match', ''

    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    assert sm.run(pair.twins, 'destination', Job()) == 0
    assert not seen
    manual = Job()
    manual.cross_exhaustive = True
    assert sm.run(pair.twins, 'destination', manual) == 1
    assert len(seen) == 1


def test_manual_search_reserves_another_compilers_donor_and_tries_its_profile(pair, monkeypatch):
    from decompnet.app import lookalike_profiles
    cfg = json.loads(pair._project('source')['config'])
    cfg['compiler']['toolchain'] = 'ido-7.1'  # The donor and destination both use -O2.
    with pair.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=1', (json.dumps(cfg),))
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status,matched_source) "
                   "VALUES(3,2,'closer_size',32,8,?,'[]',2147491840,'','matched','int closer_size(void){return 4;}')",
                   (code(62),))
    monkeypatch.setattr(sm, 'MAX_POOL', 1)
    monkeypatch.setattr(sm.toolchains, 'installed', lambda tc: tc == 'ido-7.1')
    monkeypatch.setattr(type(pair.console(pair._project('destination'))), 'compilers_for',
                        lambda self, cfg: (type('C', (), {'id': 'ido-7.1'})(),))
    tested = []

    def trial(net, agent, proj, fn, source, settings, **kw):
        tested.append((settings, source))
        return 'miss', 'score 30', 1

    monkeypatch.setattr(lookalike_profiles, 'trial', trial)
    monkeypatch.setattr(sm, 'donor_repairs', lambda *args: [])  # (its repair children: test_lookalike_profiles)
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda *args, **kw: ('miss', 'not close'))
    assert sm.run(pair.twins, 'destination', Job()) == 0
    assert not [t for t in tested if t[0]]
    manual = Job()
    manual.cross_exhaustive = True
    sm.run(pair.twins, 'destination', manual)
    with_donor = [t for t in tested if t[0]]
    assert len(with_donor) == 1 and with_donor[0][0]['toolchain'] == 'ido-7.1'
    assert 'int new(int x)' in with_donor[0][1]
    assert pair._function('new', 'destination')['status'] == 'open'
    assert pair.cfg(pair._project('destination'))['compiler']['toolchain'] == 'gcc-2.7.2-kmc'


@pytest.mark.parametrize('parallel', [False, True])
def test_a_regional_constant_change_matches_with_the_real_compiler_and_link_judge(built, tmp_path, monkeypatch, parallel):  # noqa: F811
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
    if parallel:
        from decompnet import compiler
        from decompnet.app import deep_lanes, similar_phase, workers

        from .test_speed import twin

        sibling = twin(net, 'another-region', fn['name'], 0x80401000)
        children = []
        original_start = deep_lanes.Lane.__init__

        def start(lane, *args, **kwargs):
            original_start(lane, *args, **kwargs)
            children.append(lane.process)

        monkeypatch.setattr(deep_lanes.Lane, '__init__', start)
        monkeypatch.setattr(workers, 'WORKERS', 2)
        monkeypatch.setattr(compiler, 'concurrency_limit', lambda: 2)
        job = Job(cross_tried=4)
        job.cross_exhaustive = job._parallel_donors = True
        assert similar_phase.run(net.twins, 'another-region', job) == 2
        assert len({process.pid for process in children}) == 2
        assert all(process.poll() is not None for process in children)
        assert net._function(sibling, 'another-region')['status'] == 'matched'
        assert job.progress['total'] == job.progress['done'] == 4
        assert job.cross_matched == 2 and job.cross_tried == 4 + job.similar_attempts
    else:
        assert sm.run(net.twins, 'another-region', Job()) == 1
    result = net._function(fn['name'], 'another-region')
    assert result['status'] == 'matched' and '= 61;' in result['matched_source']
    assert 'adapted from similar verified C' in result['matched_source']


def test_alternate_compiler_lead_uses_the_real_byte_judge(built, tmp_path, monkeypatch):  # noqa: F811
    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    monkeypatch.setattr(net.twins, 'after_match', lambda *args: None)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('tester')
    name = 'func_80000808'
    assert net.compile_and_diff(agent, name, DAMAGE, slug).startswith('✅ MATCH')
    pid = copy_project(net, slug, 'another-compiler')
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=json_set(config,'$.compiler.toolchain','ido-7.1') WHERE id=?", (pid,))
        db.execute("UPDATE functions SET status='asm' WHERE project_id=? AND name!=?", (pid, name))
    # Exercise the installed GCC probe without requiring IDO on this test machine.
    monkeypatch.setattr(net.twins, '_attempt_twin', lambda *a, **kw: ('miss', 'configured build did not match'))
    manual = Job()
    manual.cross_exhaustive = True
    assert sm.run(net.twins, 'another-compiler', manual) == 0
    report = net.twins.cross_game_results('another-compiler', all_matches=True)
    [lead] = report['compiler_leads']
    assert lead['target'] == name and 'byte match with gcc-2.7.2-kmc' in lead['reason']
    assert net._function(name, 'another-compiler')['status'] == 'open'


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
    monkeypatch.setattr(pair.twins, 'run', lambda *a, **kw: order.append('local'))
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


def test_another_games_donor_profile_is_a_scoped_trial_not_a_configuration_change(pair, monkeypatch):
    """A byte match under another game's profile goes through lookalike_profiles.trial, which publishes the profile
    for this function alone on an exact recheck: never the project's default, never a "review" note."""
    from decompnet.app import lookalike_profiles
    cfg = json.loads(pair._project('destination')['config'])
    cfg['compiler']['flags'] = ['-O0']
    with pair.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=2', (json.dumps(cfg),))
    monkeypatch.setattr(sm.toolchains, 'installed', lambda tc: True)
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda *args, **kwargs: ('miss', 'score 20'))
    seen = []
    def trial(net, agent, proj, fn, source, settings, **kw):
        if settings:
            seen.append((settings['toolchain'], settings['flags']))
        return 'miss', 'score 5', 1
    monkeypatch.setattr(lookalike_profiles, 'trial', trial)
    monkeypatch.setattr(sm, 'donor_repairs', lambda *args: [])  # (its repair children: test_lookalike_profiles)
    job = Job()
    assert sm.run(pair.twins, 'destination', job) == 0
    assert seen == [('gcc-2.7.2-kmc', ['-O2'])]
    assert pair.cfg(pair._project('destination'))['compiler']['flags'] == ['-O0']
    assert not pair.store.one('SELECT 1 FROM notes WHERE function_id=2')
    sm.run(pair.twins, 'destination', Job())
    assert len(seen) == 1  # cached


def test_byte_exact_leads_are_pinned_first_and_tried_with_the_donors_profile(pair, monkeypatch):
    from decompnet.app import lookalike_profiles
    cfg = json.loads(pair._project('destination')['config'])
    cfg['compiler']['flags'] = ['-O0']
    with pair.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=2', (json.dumps(cfg),))
        db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,"
                   "outcome,reason,created_at) VALUES(2,2,1,1,'compiler','byte match with gcc -O2',0)")
    assert sm.byte_exact_leads(pair, pair._project('destination'), {1: None}) == {2: [1]}
    monkeypatch.setattr(sm, 'MAX_POOL', 0)  # the shortlist alone would offer nothing
    monkeypatch.setattr(sm.toolchains, 'installed', lambda tc: True)
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda *args, **kwargs: ('miss', 'score 20'))
    seen = []
    def trial(net, agent, proj, fn, source, settings, **kw):
        if settings:
            seen.append(settings['flags'])
            with pair.store.tx() as db:
                db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=2", (source,))
            return 'match', 'byte match', 1
        return 'miss', 'score 5', 1
    monkeypatch.setattr(lookalike_profiles, 'trial', trial)
    assert sm.run(pair.twins, 'destination', Job()) == 1
    assert seen == [['-O2']]


def test_codegen_key_ignores_warnings_defines_and_order_but_not_code_flags():
    a = {'toolchain': 'ido-7.1', 'timeout': 20, 'flags': ['-O2', '-mips2', '-G', '0', '-Xcpluscomm',
                                                          '-Wab,-r4300_mul', '-woff', '649,838,712']}
    b = {'toolchain': 'ido-7.1', 'flags': ['-G', '0', '-O2', '-mips2', '-Wab,-r4300_mul', '-DNON_MATCHING']}
    assert sm.codegen_key(a) == sm.codegen_key(json.dumps(b))
    assert sm.codegen_key(a) != sm.codegen_key({**b, 'flags': ['-G', '0', '-O1', '-mips2', '-Wab,-r4300_mul']})
    assert sm.codegen_key(a) != sm.codegen_key({**b, 'flags': ['-G', '0', '-O2', '-mips2']})
    assert sm.codegen_key(a) != sm.codegen_key({**b, 'toolchain': 'ido-5.3'})


def test_shingle_index_finds_alike_code_whatever_its_size_and_orders_by_likeness():
    from decompnet import similarity
    body = [0x24820000 | i for i in range(1, 30)]
    alike = similarity.mips_shape(b''.join(w.to_bytes(4, 'big') for w in body + [0x03e00008, 0]))
    other = similarity.mips_shape(b''.join(w.to_bytes(4, 'big') for w in [0x8c820000 | i for i in range(30)] + [0x03e00008, 0]))
    index = similarity.ShingleIndex()
    index.add('other', other)
    index.add('alike', alike)
    assert index.query(alike, 5)[0] == 'alike'
    assert index.best(alike) == 1.0 and index.estimate(alike) == 1.0
    assert index.estimate(other[:4] + alike[4:]) < 1.0


def test_content_hits_come_before_the_size_ordered_shortlist(pair, monkeypatch):
    from decompnet.app.reuse_donors import Donors
    with pair.store.tx() as db:
        # Same size as the destination but unlike code; the real look-alike is larger.
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status,matched_source) "
                   "VALUES(3,1,'unlike',32,8,?,'[]',2147491840,'','matched','int unlike(void){return 4;}')",
                   (b''.join((0x8c820000 | i).to_bytes(4, 'big') for i in range(8)),))
        db.execute("UPDATE functions SET size=36,target=? WHERE id=1", (code(60) + (0x24420007).to_bytes(4, 'big'),))
    pair.twins.index_project(pair._project('source'))
    donors = Donors(pair, pair._project('destination'))
    plain = donors.candidates(32, 0, 3200, '', '', 1)
    assert plain[0]['id'] == 3
    found = donors.candidates(32, 0, 3200, '', '', 1, code=pair._function('new', 'destination')['target'])
    assert found[0]['id'] == 1


def test_best_first_orders_open_functions_by_their_closest_donor(pair, monkeypatch):
    with pair.store.tx() as db:
        # id 3: no donor like it; id 2 (the look-alike of the matched donor) has a higher id but goes first.
        db.execute("UPDATE functions SET id=9 WHERE id=2")
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status) "
                   "VALUES(3,2,'lonely',32,8,?,'[]',2147491840,'','open')",
                   (b''.join((0x8c820000 | i).to_bytes(4, 'big') for i in range(8)),))
    order = []
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda agent, proj, a, b, source, **kw:
                        (order.append(b['id']) or 'miss', 'score 20'))
    monkeypatch.setattr(sm.similarity, 'rank', lambda ops, cands, **kw: [(k, 1.0) for k in cands])
    sm.run(pair.twins, 'destination', Job())
    assert order and order[0] == 9


def test_a_new_match_reaches_its_look_alikes_in_another_active_game(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL WHERE id=1")
        db.execute("UPDATE functions SET status='matched',matched_source='int new(int x) { return x + 61; }' WHERE id=2")
    assert sm.lookalikes_elsewhere(pair, pair._project('destination'), pair._project('source'), [2]) == [1]
    ran = []
    monkeypatch.setattr(sm, 'run', lambda twins, slug, job, function_ids=None: ran.append((slug, function_ids)) or 0)
    sm.after_match(pair.twins, pair._project('destination'), [2])
    assert ('source', [1]) in ran


def test_a_transfer_that_does_not_build_is_tried_with_the_donors_missing_types(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE context_versions SET header='typedef struct Thing { int a; } Thing;' WHERE project_id=1")
        db.execute("UPDATE functions SET matched_source='int old(Thing *t) { return t->a + 60; }' WHERE id=1")
    seen = []
    def attempt(agent, proj, a, b, source, feedback=None, **kw):
        seen.append(source)
        if feedback is not None:
            feedback.update({'score': None} if 'typedef' not in source else {'score': 0})
        return 'miss', "it didn't build"
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    sm.run(pair.twins, 'destination', Job())
    assert 'typedef' not in seen[0]
    assert any('typedef struct Thing' in source and 'int new(Thing *t)' in source for source in seen)


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


def test_grouped_parameter_transfer_uses_local_sibling_before_other_repairs(pair, monkeypatch):
    from .test_parameter_transfers import SOURCE
    from .test_parameter_transfers import pair as parameter_pair

    a, b = parameter_pair()
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET project_id=2,target=?,size=?,n_insns=?,vram=?,matched_source=? WHERE id=1",
                   (a['target'],len(a['target']),len(a['target'])//4,a['vram'],SOURCE.replace('destination','old')))
        db.execute("UPDATE functions SET target=?,size=?,n_insns=?,vram=?,best_source=? WHERE id=2",
                   (b['target'],len(b['target']),len(b['target'])//4,b['vram'],
                    'void new(void *arg0, short *arg1) {}'))
    seen = []
    def attempt(agent, proj, donor, target, source, **kwargs):
        seen.append(source)
        assert donor['project_id'] == target['project_id'] == 2
        assert source.count('500') == 8
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=2", (source,))
        return 'match', ''
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    assert sm.run(pair.twins, 'destination', Job()) == 1
    assert len(seen) == 1
    assert 'donor source ' in pair.store.one('SELECT reason FROM cross_game_attempts')['reason']


def test_donor_source_is_refreshed_after_shortlisting(pair, monkeypatch):
    original = sm.similarity.rank
    def rank(*args, **kwargs):
        result = original(*args, **kwargs)
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET matched_source='int old(int x) { /* fresh */ return x + 60; }' WHERE id=1")
        return result
    monkeypatch.setattr(sm.similarity, 'rank', rank)
    seen = []
    def attempt(agent, proj, a, b, source, **kwargs):
        seen.append(source)
        return 'miss', 'not close'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    sm.run(pair.twins, 'destination', Job())
    assert seen and all('/* fresh */' in s for s in seen)


def test_similarity_links_to_complete_large_matched_source_without_truncation(pair, monkeypatch):
    import hashlib

    source = 'int old(int x) {\n' + '/* preserve source line and scope */\n'*150 + 'return x;\n}'
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET project_id=2,matched_source=? WHERE id=1', (source,))
    monkeypatch.setattr(pair, 'similar', lambda *args, **kwargs: [(pair.store.one('SELECT * FROM functions WHERE id=1'), 1.)])
    monkeypatch.setattr(pair.twins, 'suggestions', lambda *args, **kwargs: [])
    result = pair.find_similar('destination', 'new')
    assert source not in result and 'No partial C' in result
    assert 'section="matched"' in result
    from decompnet.app.agent_tokens import function_view
    assert source in function_view(pair, None, 'destination', 'old', section='matched')
    assert hashlib.sha256(source.encode()).hexdigest() in result


def test_a_donor_whose_struct_fields_sit_elsewhere_is_tried_with_them_moved(pair, monkeypatch):
    def body(offset):
        return (0x84820000 | offset).to_bytes(4, 'big') + code(60)[4:]  # lh $v0, offset($a0), then the same
    donor = ('typedef struct Actor { s16 kind; s16 hp; f32 x; } Actor;\n'
             'int old(Actor *a) { return a->hp + 60; }')
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET target=?,matched_source=? WHERE id=1", (body(2), donor))
        db.execute("UPDATE functions SET target=? WHERE id=2", (body(6),))
    seen = []
    def attempt(agent, proj, a, b, source, feedback=None, **kw):
        seen.append(source)
        if feedback is not None:  # compiled in the target's shape, the field still at the donor's offset
            feedback.update({'score': 200, 'candidate_target': body(2).hex(), 'candidate_relocs': '[]'})
        return 'miss', 'score 200'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    sm.run(pair.twins, 'destination', Job())
    assert any('char _pad0[0x4];' in source and 'int new(Actor *a)' in source for source in seen)
    assert any('(*(s16 *)((char *)a + 0x6))' in source for source in seen)


def test_step_report_counts_matches_close_misses_and_failed_builds_per_step(pair):
    rows = [(2, 'miss', 'similar code (95%); transfer; donor source x; score 40'),
            (2, 'miss', "similar code (95%); typed transfer; donor source x; it didn't build"),
            (2, 'miss', 'similar code (95%); fields; donor source x; score 300'),
            (2, 'skip', 'similar code (95%); transfer; donor source x; skip')]
    with pair.store.tx() as db:
        db.executemany("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,"
                       "outcome,reason,created_at) VALUES(2,?,1,1,?,?,0)", rows)
    report = sm.step_report(pair, pair._project('destination'))
    steps = {s['step']: s for s in report['steps']}
    assert steps['transfer'] == {'step': 'transfer', 'label': 'Donor C as it is', 'checks': 1, 'functions': 1,
                                 'matched': 0, 'rate': 0.0, 'close': 1, 'unbuilt': 0}
    assert steps['typed transfer']['unbuilt'] == 1 and steps['fields']['close'] == 0  # 300 of 800: under 90%
    assert report['close'] == [{'step': 'transfer', 'function': 'new', 'percent': 95.0, 'similarity': 95,
                                'donor': 'old', 'donor_slug': 'source', 'game': 'source'}]
    assert 'similar_steps' in pair.twins.cross_game_results('destination', all_matches=True)
    assert 'similar_steps' not in pair.twins.cross_game_results('destination')  # the overview's polling stays light


def test_numbers_that_differ_between_donors_are_set_from_the_targets_instructions(pair):
    # Three games' C for one helper: the 60 varies (60, 70), the 4 is the same in each.
    source = 'int new(int x) { return (x + 60) * 4; }'
    others = ['int new(int x) { return (x + 70) * 4; }', 'int new(int x) { if (x) return 1; return 0; }']
    compiled = code(60)
    report = {'candidate_target': compiled.hex(), 'candidate_relocs': '[]'}
    target = {'target': code(61), 'relocs': '[]'}
    assert sm.consensus(source, others, report, target, True) == 'int new(int x) { return (x + 61) * 4; }'
    assert sm.consensus(source, [others[1]], report, target, True) == ''  # no donor written alike
    assert sm.consensus(source, ['int new(int x) { return (x + 60) * 4; }'], report, target, True) == ''


def test_a_donor_calling_with_another_argument_count_is_fitted_not_skipped(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE context_versions SET header='void helper(s32 a, s32 b);' WHERE project_id=2")
        db.execute("UPDATE functions SET matched_source='int old(int x) { helper(x); return x + 60; }' WHERE id=1")
    seen = []
    monkeypatch.setattr(pair.twins, '_attempt_twin', lambda agent, proj, a, b, source, **kw:
                        (seen.append(source) or 'miss', 'score 20'))
    sm.run(pair.twins, 'destination', Job())
    assert seen and all('helper(x, 0);' in source for source in seen)


def test_matches_wait_in_the_catalog_for_an_idle_game_and_lead_its_next_pass(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL WHERE id=1")
        db.execute("UPDATE functions SET status='matched',matched_source='int new(int x) { return x + 61; }' WHERE id=2")
    assert sm.enqueue(pair, pair._project('destination'), [2], lambda slug: False) == 1
    assert sm.enqueue(pair, pair._project('destination'), [2], lambda slug: True) == 0  # active: tried at once instead
    others = {2: pair._project('destination')}
    assert sm.queued(pair, pair._project('source'), others) == {1: 1.5}
    assert sm.queued(pair, pair._project('source'), others) == {}  # read once, then gone
