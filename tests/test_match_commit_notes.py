"""Commit attribution comes from the exact recorded attempt and its own compact repair evidence."""
import hashlib
import json

from decompnet.app import match_commits
from decompnet.repair_feedback import trial

from . import test_match_commits

automatic = test_match_commits.automatic
progress = test_match_commits.progress
git = test_match_commits.git
enable = test_match_commits.enable


def test_permuter_attribution_and_annotated_repair_data_are_in_commit(automatic):
    net, root, _, _ = automatic
    enable(automatic)
    fn = net._function('f', 'game')
    agent, _ = net.local_agent(f'permuter-{fn["id"]}-lane-2')
    # Older records can already have annotated evidence for a permuter match.
    with net.store.tx() as db:
        db.execute("UPDATE agents SET origin='' WHERE id=?", (agent.id,))
    baseline = 'void f(void) {\n    int x;\n}'
    initial = {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O1']}, 'language': 'c'}
    final = {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}, 'language': 'c'}
    net._update_cfg('game', lambda cfg: cfg.update(compiler=initial['compiler']))
    net._record(fn, agent, 20, baseline, 1, effective_settings=initial)
    net._update_cfg('game', lambda cfg: cfg.update(compiler=final['compiler']))
    source = 'void f(void) {\n    return;\n}'
    with trial({'kind': 'deep local repair', 'family': 'explicit_loop_backedge',
                'base_source_sha256': hashlib.sha256(baseline.encode()).hexdigest(),
                'source_sha256': hashlib.sha256(source.encode()).hexdigest()}):
        net._record(fn, agent, 0, source, 1, matched=True, via='local-source-auto-exact', effective_settings=final)
    job = net.store.one('SELECT * FROM auto_match_commits')
    example = net.store.one('SELECT id FROM repair_examples WHERE attempt_id=?', job['attempt_id'])
    assert example is not None
    with net.store.tx() as db:
        db.execute("UPDATE repair_examples SET category='types',explanation='Use the original signed comparison' WHERE id=?", (example['id'],))
        db.execute("UPDATE agents SET name='renamed helper' WHERE id=?", (agent.id,))
    match_commits.process(net, 'game')
    message = git(root, 'log', '-1', '--format=%B')
    assert 'Matched by: deep-permuter' in message
    assert agent.name not in message
    assert 'Match method: local-source-auto-exact' in message
    assert 'Compiler profile: gcc-2.7.2-kmc (GCC 2.7.2' in message
    assert 'Compiler flags: -O2' in message and 'Compiler language: c' in message
    assert 'Repair evidence: types · failed baseline score 20 -> 0' in message
    assert 'Repair example #' not in message
    assert 'Repair explanation: Use the original signed comparison' in message
    evidence = json.loads(net.store.one('SELECT evidence FROM repair_examples WHERE id=?', example['id'])['evidence'])
    assert f"score {evidence['before']['score']} -> 0" in message
    assert 'Repair review: ' not in message
    assert 'Repair comparison: recorded inputs differ in compiler flags (-O1 -> -O2)' in message
    assert 'Repair strategy: deep local repair / explicit_loop_backedge' in message
    assert 'Final rewrite (C/C++ diff):\n@@ -2 +2 @@\n-    int x;\n+    return;' in message
    assert f"Decompdecomp-Match: {job['id']}" in message
    assert 'target_asm' not in message and 'void f(' not in message


def test_existing_pending_jobs_without_frozen_origin_are_supported(automatic):
    net, root, _, agent = automatic
    enable(automatic)
    test_match_commits.match(automatic, note='Preserved agent notes')
    job = net.store.one('SELECT * FROM auto_match_commits')
    captured = json.loads(job['snapshot']);captured.pop('origin')
    with net.store.tx() as db:
        db.execute('UPDATE auto_match_commits SET snapshot=? WHERE id=?', (json.dumps(captured), job['id']))
    test_match_commits.drain_matches(net, lambda: match_commits.process(net, 'game'))
    message = git(root, 'log', '-1', '--format=%B')
    assert message.startswith('Preserved agent notes\n\nMatched by: '+agent.name)
    assert 'Match method: agent' in message


def test_repair_evidence_for_another_source_is_not_included(automatic):
    net, root, _, agent = automatic
    enable(automatic)
    test_match_commits.match(automatic)
    job = net.store.one('SELECT * FROM auto_match_commits')
    with net.store.tx() as db:
        db.execute("INSERT INTO repair_examples(project_id,function_id,attempt_id,agent_id,created_at,evidence,explanation) VALUES(?,?,?,?,0,?,?)",
                   (job['project_id'], job['function_id'], job['attempt_id'], agent.id,
                    json.dumps({'after':{'source_sha256':'unrelated'}}), 'Do not credit unrelated evidence'))
    match_commits.process(net, 'game')
    assert 'Do not credit' not in git(root, 'log', '-1', '--format=%B')


def test_commit_uses_model_recorded_for_the_matching_session(automatic):
    from decompnet.app.agents import SCHEMA

    net, root, _, _ = automatic
    enable(automatic)
    agent, _ = net.local_agent('opencode-sol-3')
    with net.project_store('game').tx() as db:
        for statement in SCHEMA:
            db.execute(statement)
        db.execute("INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at) "
                   "VALUES(1,'game','opencode','sol',?,0)", (agent.name,))
    test_match_commits.match((*automatic[:3], agent))
    with net.project_store('game').tx() as db:
        db.execute("UPDATE helper_sessions SET model='changed later'")
    match_commits.process(net, 'game')
    message = git(root, 'log', '-1', '--format=%B')
    assert 'Matched by: sol\n' in message
    assert 'opencode-sol-3' not in message and 'changed later' not in message
