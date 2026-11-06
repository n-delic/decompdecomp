"""Captured judge evidence is immutable; explanations remain untrusted review notes."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import tools
from decompnet.app.forkengine import ForkEngine
from decompnet.network import Network, NetworkError


def test_cascade_cannot_claim_another_candidates_random_rewrite(setup):
    import hashlib

    from decompnet.repair_feedback import trial
    net,winner,_,_,fid=setup
    source='int target(void){return 7;}'
    with trial({'kind':'random rewrite','family':'perm_temp_for_expr','source_sha256':hashlib.sha256(b'other function').hexdigest()}):
        net._record(fn(net,fid),winner,0,source,1,matched=True)
    case=net.repair_feedback.listing('demo')['examples'][0]['id']
    evidence=net.repair_feedback.detail('demo',case)['evidence']
    assert evidence['provenance']['kind']=='inherited repair context'
    assert evidence['provenance']['origin']['family']=='perm_temp_for_expr'
    assert net.store.one('SELECT status FROM repair_examples WHERE id=?',case)['status']=='new'


@pytest.fixture
def setup(tmp_path):
    net = Network(tmp_path / 'app.db')
    winner = net.authenticate(net.register_agent('solver'))
    other = net.authenticate(net.register_agent('other'))
    cfg = {'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,config,big_endian,root) VALUES(?,?,?,?,?)',
                         ('demo', 'Demo', json.dumps(cfg), 1, str(tmp_path))).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns,status,unit,vram,target,meta) '
                         'VALUES(?,?,?,?,?,?,?,?,?)', (pid, 'target', 16, 4, 'open', '', 0x80001000, b'original', '{}')).lastrowid
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)', (pid, '', 1, 'typedef int word;'))
    return net, winner, other, pid, fid


def fn(net, fid):
    return net.store.one('SELECT * FROM functions WHERE id=?', fid)


def match(setup, note=''):
    net, winner, other, pid, fid = setup
    net._record(fn(net, fid), other, 20, 'int target(void) { return 1; }\n', 1)
    net._record(fn(net, fid), winner, 10, 'int target(void) { return 2; }\n', 1)
    net._record(fn(net, fid), winner, 0, 'int target(void) { return 3; }\n', 1, matched=True)
    return net.repair_feedback.listing('demo')['examples'][0]['id']


def test_automatic_evidence_chooses_closest_failed_candidate_and_retains_original(setup):
    net, winner, _, _, fid = setup
    case = match(setup)
    data = net.repair_feedback.detail('demo', case)['evidence']
    assert data['before']['score'] == 10 and 'return 2' in data['before']['source']
    assert 'return 3' in data['after']['source'] and '-int target' in data['diff']
    assert data['after']['header'] == 'typedef int word;'
    assert data['after']['settings']['compiler']['flags'] == ['-O2']
    assert data['judge'] == 'linked byte-identical' and data['target_sha256']
    # Polishing neither duplicates cases nor changes the earlier winning attempt.
    net._record(fn(net, fid), winner, 0, 'int target(void) { return 3; } /* polished */', 1, matched=True)
    assert len(net.repair_feedback.listing('demo')['examples']) == 1
    assert 'polished' not in net.repair_feedback.detail('demo', case)['evidence']['after']['source']


def test_annotations_reviews_and_exports_are_separate_from_judge(setup):
    net, winner, other, pid, fid = setup
    case = match(setup)
    with pytest.raises(NetworkError, match='by you'):
        net.repair_feedback.report(other, 'demo', 'target', 'guess')
    net.repair_feedback.report(winner, 'demo', 'target', 'Corrected a literal.', 'constants', 'Try the diff-proven constant.')
    data = net.repair_feedback.detail('demo', case)
    assert data['category'] == 'constants' and data['status'] == 'new'
    net.repair_feedback.review('demo', case, 'reviewed')
    assert net.repair_feedback.listing('demo', category='constants', status='reviewed')['groups'][0]['count'] == 1
    summary = json.loads(''.join(net.repair_feedback.export('demo')))
    full = json.loads(''.join(net.repair_feedback.export('demo', include_source=True)))
    assert 'source' not in summary['examples'][0]['evidence']['after']
    assert 'diff' not in summary['examples'][0]['evidence']
    assert 'return 3' in full['examples'][0]['evidence']['after']['source']
    assert fn(net, fid)['status'] == 'matched'
    with pytest.raises(NetworkError):
        net.repair_feedback.review('demo', case, 'enable_rule')


def test_inline_note_only_survives_a_new_verified_match(setup, monkeypatch):
    net, winner, _, pid, fid = setup
    def attempt(agent, name, source, slug):
        net._record(fn(net, fid), agent, 0 if source == 'good' else 5, source, 1, matched=source == 'good')
        return 'result'
    monkeypatch.setattr(net, 'compile_and_diff', attempt, raising=False)
    tools._compile(net, winner, 'demo', 'target', 'bad', repair_note='Not proven yet')
    assert not net.repair_feedback.listing('demo')['examples']
    result = tools._compile(net, winner, 'demo', 'target', 'good', 'Fixed argument type.', 'types', 'Try signedness variants.')
    assert 'explanation saved' in result
    row = net.repair_feedback.listing('demo')['examples'][0]
    assert row['explanation'] == 'Fixed argument type.'
    tools._compile(net, winner, 'demo', 'target', 'good', 'Polished.', 'other')
    assert net.repair_feedback.listing('demo')['examples'][0]['explanation'] == 'Fixed argument type.'


def test_first_try_and_changed_context_are_not_presented_as_isolated_code_repairs(setup):
    net, winner, _, pid, fid = setup
    net._record(fn(net, fid), winner, 0, 'first try', 1, matched=True)
    data = net.repair_feedback.detail('demo', 1)['evidence']
    assert data['before'] is None
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=?", (fid,))
    net._record(fn(net, fid), winner, 9, 'before', 1)
    with net.store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
        cfg['compiler']['flags'] = ['-O1']
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)', (pid, '', 2, 'typedef unsigned int word;'))
    net._record(fn(net, fid), winner, 0, 'after', 2, matched=True)
    latest = net.repair_feedback.detail('demo', net.repair_feedback.listing('demo')['examples'][0]['id'])['evidence']
    assert latest['before']['header'] != latest['after']['header']
    assert latest['before']['settings']['compiler']['flags'] != latest['after']['settings']['compiler']['flags']


def test_imported_unit_diff_matches_are_captured_with_explicit_build_context_limits(setup):
    net, winner, _, pid, fid = setup
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps({'kind': 'fork', 'platform': 'gc_wii', 'compiler': 'Metrowerks'}), pid))
    engine = SimpleNamespace(net=net, _find=lambda text, names: (None, (0, len(text))),
                             _names=lambda meta, row: [], _meta=lambda row: {})
    with net.store.tx() as db:
        ForkEngine._matched(engine, db, net._project('demo'), fn(net, fid), winner, 'void target(void) {}')
    data = net.repair_feedback.detail('demo', 1)['evidence']
    assert data['judge'] == 'objdiff 100%'
    assert data['after']['source'] == 'void target(void) {}'
    assert not data['after']['settings']['per_file_flags_recorded']


def test_delete_cleans_evidence_and_attempt_settings(setup):
    net, winner, _, pid, fid = setup
    match(setup)
    with net.store.tx() as db:
        db.execute('DELETE FROM attempts WHERE function_id=?', (fid,))
    assert net.store.one('SELECT COUNT(*) AS n FROM repair_examples')['n'] == 0
    assert net.store.one('SELECT COUNT(*) AS n FROM repair_attempt_settings')['n'] == 0


def test_paging_comparison_filter_and_manual_review_notes(setup):
    net, winner, _, pid, fid = setup
    net._record(fn(net, fid), winner, 0, 'first', 1, matched=True)
    assert not net.repair_feedback.listing('demo', comparable=True)['examples']
    for number in range(28):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='open' WHERE id=?", (fid,))
        net._record(fn(net, fid), winner, 10, f'failed {number}', 1)
        net._record(fn(net, fid), winner, 0, f'matched {number+100}', 1, matched=True)
    page = net.repair_feedback.listing('demo', comparable=True)
    assert len(page['examples']) == 25 and page['more']
    assert page['total'] == 28
    older = net.repair_feedback.listing('demo', before=page['examples'][-1]['id'], comparable=True)
    assert len(older['examples']) == 3 and not older['more']
    assert older['total'] == 28
    case = page['examples'][0]['id']
    net.repair_feedback.review('demo', case, 'reviewed', 'casts', 'Try as an AST rewrite; implementation PR pending.')
    assert net.repair_feedback.listing('demo', status='reviewed', category='casts', comparable=True)['total'] == 1
    assert net.repair_feedback.listing('demo', status='new', comparable=True)['total'] == 27
    data = net.repair_feedback.detail('demo', case)
    assert data['category'] == 'casts' and 'AST rewrite' in data['review_note']
    net.repair_feedback.report(winner, 'demo', 'target', 'Revised explanation.', 'types')
    assert net.repair_feedback.detail('demo', case)['status'] == 'new'


def test_deleted_old_attempt_cannot_be_replaced_with_unrelated_reused_id(setup):
    net, winner, _, pid, fid = setup
    case = match(setup)
    before = net.repair_feedback.detail('demo', case)['before_attempt_id']
    with net.store.tx() as db:
        db.execute('DELETE FROM attempts WHERE id=?', (before,))
        db.execute('INSERT INTO attempts(id,function_id,source) VALUES(?,?,?)', (before, fid, 'unrelated reused record'))
    assert net.repair_feedback.detail('demo', case)['evidence']['before']['source'] is None


def test_mcp_and_api_accept_inline_feedback_without_an_extra_model_call(setup, monkeypatch):
    import anyio

    from decompnet.agents.api import api_tool_schemas
    net, winner, _, _, fid = setup
    def attempt(agent, name, source, slug):
        net._record(fn(net, fid), agent, 0, source, 1, matched=True)
        return 'matched'
    monkeypatch.setattr(net, 'compile_and_diff', attempt, raising=False)
    monkeypatch.setattr(tools, '_named', lambda net, agent, slug, args, name: args)
    mcp = tools.build_mcp(net, lambda context: (winner, 'demo'))
    anyio.run(mcp.call_tool, 'compile_and_diff', {'function': 'target', 'source': 'winning C',
                                               'repair_note': 'Changed signedness.', 'repair_category': 'types',
                                               'repair_automation': 'Try a signedness repair.'})
    row = net.repair_feedback.listing('demo')['examples'][0]
    assert row['explanation'] == 'Changed signedness.'
    schema = {row['function']['name']: row['function'] for row in api_tool_schemas(names=['compile_and_diff', 'report_repair'])}
    assert 'repair_note' in schema['compile_and_diff']['parameters']['properties']
    assert 'repair_note' not in schema['compile_and_diff']['parameters']['required']
    assert 'report_repair' in schema


def test_header_snapshot_records_global_and_unit_context_scopes(setup):
    net, winner, _, pid, fid = setup
    with net.store.tx() as db:
        db.execute('UPDATE functions SET unit=? WHERE id=?', ('main', fid))
    net._record(fn(net, fid), winner, 10, 'before', 1)
    with net.store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)',
                   (pid, 'main', 2, 'typedef unsigned int word;'))
    net._record(fn(net, fid), winner, 0, 'after', 2, matched=True)
    evidence = net.repair_feedback.detail('demo', 1)['evidence']
    assert evidence['before']['header'] == 'typedef int word;'
    assert evidence['after']['header'] == 'typedef unsigned int word;'


def test_comparable_failure_previous_trial_effective_settings_and_provenance(setup):
    from decompnet.repair_feedback import trial
    net,winner,_,pid,fid=setup
    effective={'compiler':{'toolchain':'example','flags':['-O2']},'effective_command':['cc','-O2','{src}']}
    def configure(compiler):
        with net.store.tx() as db:
            cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
            cfg['compiler'] = compiler
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
    configure(effective['compiler'])
    net._record(fn(net,fid),winner,20,'comparable',1,effective_settings=effective)
    configure({'toolchain': 'other', 'flags': ['-O1']})
    net._record(fn(net,fid),winner,1,'different settings',1,effective_settings={'compiler':{'toolchain':'other','flags':['-O1']}})
    configure(effective['compiler'])

    with trial({'kind':'agent batch','family':'casts','variant':2}):
        net._record(fn(net,fid),winner,0,'matched',1,matched=True,effective_settings=effective)
    case=net.repair_feedback.listing('demo')['examples'][0]['id']
    evidence=net.repair_feedback.detail('demo',case)['evidence']
    assert evidence['before']['source']=='comparable' and evidence['comparable_settings']
    assert evidence['previous']['source']=='different settings'
    assert evidence['after']['settings']['effective_command']==['cc','-O2','{src}']
    assert evidence['provenance']['family']=='casts' and evidence['provenance']['variant']==2
    assert 'different settings' in evidence['previous_diff']
    exported=net.repair_feedback.detail('demo',case,include_source=False)['evidence']
    assert 'source' not in exported['previous'] and 'header' not in exported['previous']


def test_compile_failure_settings_are_recorded_without_a_repair_case(setup):
    net,winner,_,pid,fid=setup
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps({'compiler':{'cmd':['test','-O1']}}),pid))
    net._record(fn(net,fid),winner,None,'bad C',1,effective_settings={'compiler':{'cmd':['test','-O1']}})
    assert not net.repair_feedback.listing('demo')['examples']
    row=net.store.one('SELECT settings FROM repair_attempt_settings ORDER BY attempt_id DESC LIMIT 1')
    assert json.loads(row['settings'])['compiler']['cmd']==['test','-O1']
    net._record(fn(net,fid),winner,0,'valid C',1,matched=True)
    case=net.repair_feedback.listing('demo')['examples'][0]['id']
    previous=net.repair_feedback.detail('demo',case)['evidence']['previous']
    assert previous['source']=='bad C' and previous['score'] is None


def test_repair_detail_explains_comment_only_changes_without_automatic_dismissal(setup):
    net,winner,_,_,fid=setup
    net._record(fn(net,fid),winner,10,'/* compiler A */\nint target(){return 1;}',1)
    net._record(fn(net,fid),winner,0,'/* compiler B */\nint target(){return 1;}',1,matched=True)
    case=net.repair_feedback.listing('demo')['examples'][0]['id']
    detail=net.repair_feedback.detail('demo',case)
    assert detail['evidence']['source_change']=='comments_or_spacing'
    assert 'source line boundaries preserved' in detail['evidence']['source_change_label']
    assert detail['status']=='new'  # descriptive evidence never auto-approves a proposed rule
    summary=net.repair_feedback.detail('demo',case,include_source=False)
    assert 'source' not in summary['evidence']['before']


@pytest.mark.parametrize('path',['quiet_transfer','machine_draft','machine_refined','machine_buffer','machine_buffer_indexed','machine_local_context'])
def test_autopilot_capture_uses_actual_compile_settings_instead_of_project_snapshot(setup,monkeypatch,path):
    from decompnet import scoring
    from decompnet.app.engine import autopilot
    from decompnet.app.engine.autopilot import AutopilotMixin
    net,winner,_,pid,fid=setup
    effective={'compiler':{'toolchain':'actual-compiler','flags':['-O2','-g3']},
               'effective_command':['actual-cc','-g3','{src}'],'language':'c',
               'settings_source':'compile invocation snapshot'}
    # A per-function override differs from the project default without being an in-flight stale profile.
    with net.store.tx() as db:
        cfg=json.loads(db.execute('SELECT config FROM projects WHERE id=?',(pid,)).fetchone()['config'])
        cfg['compiler_ranges']=[{'start':0x80001000,'end':0x80001010,**effective['compiler']}]
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))

    monkeypatch.setattr(net, '_jtbl_asm', lambda *args: [], raising=False)
    result=SimpleNamespace(ok=True,obj=b'object',effective_settings=effective)
    monkeypatch.setattr(net,'_compile',lambda *args:result)
    monkeypatch.setattr(net,'_evaluate',lambda *args:(SimpleNamespace(code=b'code',relocs=[]),True,[]))
    monkeypatch.setattr(net,'_target_obj',lambda *args:object())
    monkeypatch.setattr(autopilot.scoring,'diff',lambda *args:scoring.DiffResult(0,4,[],True))
    proj=net.store.one('SELECT * FROM projects WHERE id=?',pid)
    if path=='quiet_transfer':
        assert AutopilotMixin.try_quietly(net,winner,proj,fn(net,fid),'int target(void){return 0;}')['matched']
    else:
        monkeypatch.setattr(net,'_seed_for',lambda *args:None,raising=False)
        monkeypatch.setattr(net,'_draft_plan',lambda *args,**kwargs:(None,'gcc-2.7.2-kmc',''),raising=False)
        monkeypatch.setattr(net,'cfg',lambda p:json.loads(p['config']),raising=False)
        monkeypatch.setattr(net,'_asm_text',lambda *args:'',raising=False)
        monkeypatch.setattr(autopilot.seeds,'generate_from_asm',lambda *args,**kwargs:'int target(void){return 0;}')
        if path!='machine_local_context':
            with net.store.tx() as db:
                db.execute('UPDATE functions SET best_source=? WHERE id=?',('short sample(int);',fid))
            def unexpected_fallback(*args):
                raise AssertionError('successful standard drafts must not generate the fallback')
            monkeypatch.setattr(autopilot,'candidate_context',unexpected_fallback)
        if path=='machine_local_context':
            monkeypatch.setattr(autopilot,'candidate_context',lambda *args:'synthetic local context')
            with net.store.tx() as db:
                db.execute('UPDATE functions SET best_source=? WHERE id=?',
                           ('short sample(int); int target(void){return sample(0);}',fid))
            monkeypatch.setattr(net,'_current_names',lambda *args:{'sample','target'},raising=False)
            monkeypatch.setattr(autopilot.seeds,'generate_from_asm',
                                lambda *args,**kwargs:('/* LOCAL */ int target(void){return 0;}'
                                                      if args[3] == 'synthetic local context' else 'int target(void){return 1;}'))
            monkeypatch.setattr(autopilot.m2cfix,'refine_draft',lambda *args:None)
            monkeypatch.setattr(net,'_compile',lambda *args:result if 'LOCAL' in args[2] else SimpleNamespace(ok=False,obj=None,log="synthetic compiler refusal"))
        if path=='machine_refined':
            monkeypatch.setattr(autopilot.m2cfix,'refine_draft',lambda *args:'/* REPAIRED */ int target(void){return 0;}')
            monkeypatch.setattr(net,'_compile',lambda *args:result if 'REPAIRED' in args[2] else SimpleNamespace(ok=False,obj=None,log="synthetic compiler refusal"))
        if path in ('machine_buffer','machine_buffer_indexed'):
            monkeypatch.setattr(autopilot.m2cfix,'refine_draft',lambda *args:None)
            monkeypatch.setattr(autopilot.m2cfix,'stack_array',lambda source,name,canonical:'/* BUFFER */ int target(void){return 0;}' if canonical==(path=='machine_buffer_indexed') else None)
            monkeypatch.setattr(net,'_compile',lambda *args:result if 'BUFFER' in args[2] else SimpleNamespace(ok=False,obj=None,log="synthetic compiler refusal"))
        assert AutopilotMixin._autopilot_one(net,winner,proj,fid)=='match'
    case=net.repair_feedback.listing('demo')['examples'][0]['id']
    detail=net.repair_feedback.detail('demo',case)
    settings=detail['evidence']['after']['settings']
    if path!='quiet_transfer':
        family={'machine_draft':'raw_m2c','machine_refined':'refined_m2c','machine_buffer':'stack_array','machine_buffer_indexed':'stack_array_indexed','machine_local_context':'local_context_m2c'}[path]
        assert detail['evidence']['provenance']=={'kind':'machine draft','family':family}
        assert detail['status']=='implemented'
    assert settings['compiler']==effective['compiler']
    assert settings['effective_command']==effective['effective_command']
    assert settings['settings_source']=='compile invocation snapshot'
    assert not net.store.all('SELECT * FROM claims')


def test_unknown_inline_category_preserves_patch_and_original_annotation(setup,monkeypatch):
    net,winner,_,_,fid=setup
    def attempt(agent,name,source,slug):
        net._record(fn(net,fid),agent,0,source,1,matched=True)
        return 'matched'
    monkeypatch.setattr(net,'compile_and_diff',attempt,raising=False)
    tools._compile(net,winner,'demo','target','winning C',repair_note='Preserved load order.',repair_category='load_store_order')
    row=net.repair_feedback.listing('demo')['examples'][0]
    assert row['category']=='other'
    assert row['explanation']=='[Original category: load_store_order] Preserved load order.'


def test_unchanged_c_and_missing_baselines_are_separate_from_source_repairs(setup):
    net,winner,_,_,fid=setup
    net._record(fn(net,fid),winner,10,'int target(){return 1;}',1)
    net._record(fn(net,fid),winner,0,'int target(){return 1;}',1,matched=True)
    assert net.repair_feedback.listing('demo')['examples'][0]['evidence_kind']=='same source / settings'
    assert not net.repair_feedback.listing('demo',comparable=True)['examples']


def test_installed_deterministic_successes_are_retained_as_implemented(setup):
    from decompnet.repair_feedback import trial
    net,winner,_,_,fid=setup
    net._record(fn(net,fid),winner,10,'int target(){return 1;}',1)
    with trial({'kind':'deterministic draft repair','family':'constant_local'}):
        net._record(fn(net,fid),winner,0,'int target(){return 2;}',1,matched=True)
    case=net.repair_feedback.listing('demo')['examples'][0]
    assert case['status']=='implemented' and case['automation_known']
    assert not net.repair_feedback.listing('demo',status='new')['examples']


def test_legacy_automatic_captures_are_triaged_without_overwriting_review_notes(setup):
    from decompnet.repair_feedback import RepairFeedback, trial
    net,winner,_,_,fid=setup
    net._record(fn(net,fid),winner,10,'int target(){return 1;}',1)
    with trial({'kind':'deterministic draft repair','family':'constant_local'}):
        net._record(fn(net,fid),winner,0,'int target(){return 2;}',1,matched=True)
    case=net.repair_feedback.listing('demo')['examples'][0]['id']
    with net.store.tx() as db:
        db.execute("UPDATE repair_examples SET status='new',review_note='' WHERE id=?",(case,))
    RepairFeedback(net)
    assert net.repair_feedback.detail('demo',case)['status']=='implemented'
    with net.store.tx() as db:
        db.execute("UPDATE repair_examples SET status='new',review_note='Investigate further' WHERE id=?",(case,))
    RepairFeedback(net)
    assert net.repair_feedback.detail('demo',case)['status']=='new'


def test_classify_new_is_previewable_idempotent_and_preserves_manual_reviews(setup):
    net, *_ = setup
    case = match(setup)
    preview = net.repair_feedback.classify_new('demo')
    assert preview['total'] == 1 and not preview['applied']
    assert net.repair_feedback.detail('demo', case)['status'] == 'new'
    assert preview['examples'][0]['category'] == 'constants'
    applied = net.repair_feedback.classify_new('demo', True)
    assert applied['applied'] and net.repair_feedback.detail('demo', case)['status'] == 'reviewed'
    assert net.repair_feedback.classify_new('demo', True)['total'] == 0
    net.repair_feedback.review('demo', case, 'new', note='Human investigation pending.')
    assert net.repair_feedback.classify_new('demo', True)['total'] == 0
    assert net.repair_feedback.detail('demo', case)['review_note'] == 'Human investigation pending.'


def test_duplicate_triage_requires_identical_captured_build_evidence(setup):
    net, *_ = setup
    case = match(setup)
    with net.store.tx() as db:
        db.execute('''INSERT INTO repair_examples(project_id,function_id,attempt_id,before_attempt_id,agent_id,created_at,evidence)
            SELECT project_id,function_id,attempt_id,before_attempt_id,agent_id,created_at,evidence FROM repair_examples WHERE id=?''', (case,))
    result = net.repair_feedback.classify_new('demo', True)
    assert [p['status'] for p in result['examples']] == ['reviewed', 'dismissed']
    assert net.store.one('SELECT COUNT(*) n FROM repair_examples')['n'] == 2


def test_prior_reviewed_evidence_is_used_when_grouping_new_duplicates(setup):
    net, *_ = setup
    case = match(setup)
    net.repair_feedback.classify_new('demo', True)
    with net.store.tx() as db:
        db.execute('''INSERT INTO repair_examples(project_id,function_id,attempt_id,before_attempt_id,agent_id,created_at,evidence)
            SELECT project_id,function_id,attempt_id,before_attempt_id,agent_id,created_at,evidence FROM repair_examples WHERE id=?''', (case,))
    plan = net.repair_feedback.classify_new('demo')['examples']
    assert len(plan) == 1 and plan[0]['status'] == 'dismissed'
    assert f'#{case}' in plan[0]['note']


def test_review_filter_requires_comparable_settings_and_groups_equivalent_captures(setup):
    net, winner, _, _, fid = setup
    net._record(fn(net,fid),winner,10,'before',1)
    net._record(fn(net,fid),winner,0,'after',1,matched=True)
    row = net.store.one('SELECT * FROM repair_examples ORDER BY id DESC LIMIT 1')
    evidence = json.loads(row['evidence'])
    evidence['comparable_settings'] = False
    with net.store.tx() as db:
        db.execute('UPDATE repair_examples SET evidence=? WHERE id=?', (json.dumps(evidence),row['id']))
    assert not net.repair_feedback.listing('demo',comparable=True)['examples']
    evidence['comparable_settings'] = True
    with net.store.tx() as db:
        db.execute('UPDATE repair_examples SET evidence=? WHERE id=?', (json.dumps(evidence),row['id']))
        db.execute('INSERT INTO repair_examples(project_id,function_id,attempt_id,before_attempt_id,agent_id,created_at,evidence) SELECT project_id,function_id,attempt_id,before_attempt_id,agent_id,created_at,evidence FROM repair_examples WHERE id=?', (row['id'],))
    examples = net.repair_feedback.listing('demo',comparable=True)['examples']
    assert len(examples) == 1 and examples[0]['repetitions'] == 2
    assert net.repair_feedback.listing('demo',comparable=True)['total'] == 1


def test_converter_infrastructure_failure_does_not_consume_attempt_budget(setup, monkeypatch):
    net, winner, _, _, fid = setup
    before = net.store.one('SELECT COUNT(*) n FROM attempts')['n']
    monkeypatch.setattr(net,'_compile',lambda *args:SimpleNamespace(ok=False,log="Unknown section type '.rodata'"))
    with pytest.raises(NetworkError,match='did not consume a repair attempt'):
        net.compile_and_diff(winner,'target','int target(void){return 1;}','demo')
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n'] == before
    assert net.store.one('SELECT attempts FROM functions WHERE id=?',fid)['attempts'] == 0


def test_build_activity_groups_only_same_unit_minute_and_settings(setup):
    net, _, _, pid, fid = setup
    match(setup)
    row = net.store.one('SELECT * FROM repair_examples ORDER BY id DESC LIMIT 1')
    evidence = json.loads(row['evidence'])
    with net.store.tx() as db:
        for number, stamp in enumerate((600, 601, 660)):
            other = db.execute('INSERT INTO functions(project_id,name,status,unit) VALUES(?,?,?,?)',
                               (pid, f'build_{number}', 'matched', 'shared')).lastrowid
            item = json.loads(json.dumps(evidence));item.update(via='unit diff', unit='shared', function=f'build_{number}', target_sha256=str(number))
            item['after']['source_sha256']=str(number)
            db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(?,?,?,?)',
                       (pid, other, stamp, json.dumps(item)))
    records = net.repair_feedback.listing('demo')['examples']
    builds = [r for r in records if r['evidence_kind']=='build report']
    assert sorted(r['repetitions'] for r in builds) == [1,2]
    assert len([r for r in records if r['evidence_kind']!='build report']) == 1
    assert net.store.one('SELECT COUNT(*) AS n FROM repair_examples')['n'] == 4


def test_reopened_identical_match_keeps_original_review_and_analysis(setup, monkeypatch):
    from decompnet.app import repair_outcomes
    net, winner, _, _, fid = setup
    scheduled = []
    monkeypatch.setattr(repair_outcomes, 'schedule', lambda *args: scheduled.append(args[1]))
    case = match(setup)
    net.repair_feedback.review('demo', case, 'reviewed', 'constants', 'Keep this review.')
    source = net.repair_feedback.detail('demo', case)['evidence']['after']['source']
    count = net.store.one('SELECT COUNT(*) n FROM attempts')['n']
    for _ in range(3):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='open' WHERE id=?", (fid,))
        net._record(fn(net, fid), winner, 0, source, 1, matched=True)
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n'] == count + 3
    assert net.store.one('SELECT COUNT(*) n FROM repair_examples')['n'] == 1
    assert scheduled == [case]
    row = net.repair_feedback.detail('demo', case)
    assert row['status'] == 'reviewed' and row['review_note'] == 'Keep this review.'
    assert fn(net, fid)['status'] == 'matched'


@pytest.mark.parametrize('change', ['target', 'header', 'compiler', 'source', 'failure', 'agent', 'provenance', 'via', 'project'])
def test_recheck_keeps_distinct_or_incomplete_evidence(setup, change):
    from decompnet.repair_feedback import trial
    net, winner, other, pid, fid = setup
    case = match(setup)
    source = net.repair_feedback.detail('demo', case)['evidence']['after']['source']
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=?", (fid,))
        if change == 'target':
            db.execute('UPDATE functions SET target=? WHERE id=?', (b'changed target', fid))
        if change == 'header':
            db.execute('UPDATE context_versions SET header=? WHERE project_id=?', ('typedef long word;', pid))
        if change in ('compiler', 'project'):
            cfg = json.loads(net.store.one('SELECT config FROM projects WHERE id=?', pid)['config'])
            if change == 'compiler': cfg['compiler']['flags'] = ['-O1']
            else: cfg['kind'] = 'fork'
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
    if change == 'failure': net._record(fn(net, fid), winner, 5, 'new failed source', 1)
    if change == 'source': source += ' /* new source */'
    with trial({'kind': 'new provenance'} if change == 'provenance' else None):
        net._record(fn(net, fid), other if change == 'agent' else winner, 0, source, 1,
                    matched=True, via='recheck' if change == 'via' else 'agent')
    assert net.store.one('SELECT COUNT(*) n FROM repair_examples')['n'] == 2


def test_identical_imported_rechecks_remain_separate_without_build_snapshots(setup):
    net, winner, _, pid, fid = setup
    with net.store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
        cfg['kind'] = 'fork'
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
    case = match(setup)
    source = net.repair_feedback.detail('demo', case)['evidence']['after']['source']
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=?", (fid,))
    net._record(fn(net, fid), winner, 0, source, 1, matched=True)
    assert net.store.one('SELECT COUNT(*) n FROM repair_examples')['n'] == 2


def test_recording_selects_same_image_failure_across_container_metadata(setup):
    from tests.test_repair_comparison import snapshot
    net, winner, _, pid, fid = setup
    effective = snapshot()
    def configure(settings):
        with net.store.tx() as db:
            cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
            cfg['compiler'] = settings['compiler']
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg),pid))
    configure(effective)
    net._record(fn(net,fid), winner, 20, 'same judging inputs', 1, effective_settings=effective)
    altered = json.loads(json.dumps(effective))
    altered['compiler']['flags'] = ['-O1']
    configure(altered)
    net._record(fn(net,fid), winner, 1, 'different compiler inputs', 1, effective_settings=altered)
    effective['rom']['byte_order'] = 'z64'
    configure(effective)
    net._record(fn(net,fid), winner, 0, 'matched', 1, matched=True, effective_settings=effective)
    case_id = net.repair_feedback.listing('demo', comparable=True)['examples'][0]['id']
    evidence = net.repair_feedback.detail('demo', case_id)['evidence']
    assert evidence['comparable_settings']
    assert evidence['before']['source'] == 'same judging inputs'
    assert evidence['before']['settings']['rom']['byte_order'] == 'v64'
    assert evidence['after']['settings']['rom']['byte_order'] == 'z64'
    assert evidence['previous']['source'] == 'different compiler inputs'


def test_repeated_installed_rule_keeps_match_but_skips_extra_capture_and_analysis(setup,monkeypatch):
    from decompnet.app import repair_outcomes
    from decompnet.repair_feedback import trial
    net,winner,_,pid,fid=setup
    scheduled=[]
    monkeypatch.setattr(repair_outcomes,'schedule',lambda *args:scheduled.append(args))
    for n in range(3):
        with net.store.tx() as db:
            identity=db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,vram,target,meta) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid,f'case{n}',16,4,'open','',0x80002000+n*16,b'original','{}')).lastrowid
        net._record(fn(net,identity),winner,10,f'int case{n}(int x){{return x;}}',1)
        with trial({'kind':'deep local repair','family':'unsigned_return'}):
            net._record(fn(net,identity),winner,0,f'unsigned case{n}(int x){{return x;}}',1,matched=True)
        assert fn(net,identity)['status']=='matched'
    assert net.store.one('SELECT COUNT(*) n FROM repair_examples')['n']==1
    assert len(scheduled)==1
    row=net.store.one('SELECT successes,retained FROM repair_capture_groups')
    assert tuple(row)==(3,1)
