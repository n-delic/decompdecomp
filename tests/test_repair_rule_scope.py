"""Implementation fingerprints are advisory and fresh for each foreground tool call."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest

from decompnet import claim_stalls
from decompnet.app import tools
from tests.test_agent_workflow_efficiency import game


def test_one_tool_call_scans_once_and_next_call_is_fresh(tmp_path, monkeypatch):
    net, agent, _ = game(tmp_path)
    calls = []
    revision = [1]
    def rules():
        calls.append(True)
        return [('repair.py', str(revision[0]))]
    monkeypatch.setattr(claim_stalls, '_repair_rules', rules)
    def handler(*args, **kwargs):
        first = claim_stalls.repair_rules()
        for _ in range(100):assert claim_stalls.repair_rules() == first
        first.append(('caller-edit', 'not retained'))
        assert len(claim_stalls.repair_rules()) == 1
        return claim_stalls.repair_rules()[0][1]
    monkeypatch.setitem(tools.HANDLERS, 'project_info', handler)
    assert tools._dispatch(net, agent, 'game', 'project_info', {}, count_usage=False) == '1'
    assert len(calls) == 1
    revision[0] = 2
    assert tools._dispatch(net, agent, 'game', 'project_info', {}, count_usage=False) == '2'
    assert len(calls) == 2


def test_scope_detects_file_edits_next_operation_and_cleans_up_failure(tmp_path, monkeypatch):
    path = tmp_path / 'rule.py'
    path.write_text('first')
    monkeypatch.setattr(claim_stalls, '_repair_rules', lambda: [('rule.py', claim_stalls.file_digest(path))])
    with pytest.raises(ValueError, match='cancel'):
        with claim_stalls.rule_snapshot():
            first = claim_stalls.repair_rules()
            with claim_stalls.rule_snapshot():assert claim_stalls.repair_rules() == first
            path.write_text('other')
            assert claim_stalls.repair_rules() == first
            raise ValueError('cancel')
    with claim_stalls.rule_snapshot():assert claim_stalls.repair_rules() != first


def test_implementation_replacement_invalidates_snapshot(monkeypatch):
    monkeypatch.setattr(claim_stalls, '_repair_rules', lambda: [('rule.py', 'first')])
    with claim_stalls.rule_snapshot():
        assert claim_stalls.repair_rules() == [('rule.py', 'first')]
        monkeypatch.setattr(claim_stalls, '_repair_rules', lambda: [('rule.py', 'other')])
        assert claim_stalls.repair_rules() == [('rule.py', 'other')]


def test_copied_context_does_not_share_fingerprint_between_threads(monkeypatch):
    calls = []
    def rules():
        calls.append(True)
        return [('rule.py', str(len(calls)))]
    monkeypatch.setattr(claim_stalls, '_repair_rules', rules)
    monkeypatch.setattr(claim_stalls, '_recent', None)
    with claim_stalls.rule_snapshot():
        assert claim_stalls.repair_rules() == [('rule.py', '1')]
        context = copy_context()
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(context.run, claim_stalls.repair_rules).result() == [('rule.py', '2')]
        assert claim_stalls.repair_rules() == [('rule.py', '1')]
    # outside a snapshot a caller reuses the stamp the other thread just made for a few seconds (b1c7611da); this
    # asserts a fresh one after they pass
    claim_stalls._recent = None
    assert claim_stalls.repair_rules() == [('rule.py', '3')]
