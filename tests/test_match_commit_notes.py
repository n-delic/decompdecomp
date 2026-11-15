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
    net, root, _, agent = automatic
    enable(automatic)
    fn = net._function('f', 'game')
    net._record(fn, agent, 20, 'void f(void) { int x; }', 1)
    source = 'void f(void) { return; }'
    with trial({'kind':'local repair', 'family':'signed_comparison', 'source_sha256':hashlib.sha256(source.encode()).hexdigest()}):
        net._record(fn, agent, 0, source, 1, matched=True, via='permuter')
    job = net.store.one('SELECT * FROM auto_match_commits')
    example = net.store.one('SELECT id FROM repair_examples WHERE attempt_id=?', job['attempt_id'])
    assert example is not None
    with net.store.tx() as db:
        db.execute("UPDATE repair_examples SET category='types',explanation='Use the original signed comparison' WHERE id=?", (example['id'],))
        db.execute("UPDATE agents SET name='renamed helper' WHERE id=?", (agent.id,))
    match_commits.process(net, 'game')
    message = git(root, 'log', '-1', '--format=%B')
    assert 'Matched by: '+agent.name in message and 'Match method: permuter' in message
    assert f"Repair example #{example['id']} (local record; types)" in message
    assert 'Repair explanation: Use the original signed comparison' in message
    evidence = json.loads(net.store.one('SELECT evidence FROM repair_examples WHERE id=?', example['id'])['evidence'])
    assert f"score {evidence['before']['score']} -> 0" in message
    assert 'Repair review: ' in message and 'Repair comparison: ' in message
    assert 'Repair strategy: local repair / signed_comparison' in message
    assert 'target_asm' not in message and 'void f(' not in message


def test_existing_pending_jobs_without_frozen_origin_are_supported(automatic):
    net, root, _, agent = automatic
    enable(automatic)
    test_match_commits.match(automatic, note='Preserved agent notes')
    job = net.store.one('SELECT * FROM auto_match_commits')
    captured = json.loads(job['snapshot']);captured.pop('origin')
    with net.store.tx() as db:
        db.execute('UPDATE auto_match_commits SET snapshot=? WHERE id=?', (json.dumps(captured), job['id']))
    match_commits.process(net, 'game')
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
