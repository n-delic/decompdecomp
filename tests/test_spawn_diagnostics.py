"""Capacity metadata must not leak command arguments or convert launch errors into compiler results."""
import errno
import logging
from types import SimpleNamespace

import pytest

from decompnet import compiler
from decompnet import spawn_diagnostics as diagnostics
from decompnet.app.agents import Manager, Session
from tests.test_agent_workflow_efficiency import game


@pytest.fixture(autouse=True)
def clean_diagnostics(monkeypatch):
    monkeypatch.setattr(diagnostics, '_last', {})
    monkeypatch.setattr(diagnostics, '_failures', {})
    monkeypatch.setattr(diagnostics, '_process_limits', lambda: (17, 19))


def test_capacity_context_is_redacted_and_rate_bounded(monkeypatch, caplog):
    clock = [100.]
    monkeypatch.setattr(diagnostics.time, 'monotonic', lambda: clock[0])
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    error = BlockingIOError(errno.EAGAIN, 'private-response-body', '/private/credential')
    for _ in range(5):diagnostics.record(error, 'compiler', active_compiles=3, compile_limit=4)
    messages = [r.message for r in caplog.records]
    assert len(messages) == 1
    assert 'nproc_soft=17 nproc_hard=19 active_compiles=3 compile_limit=4 failures=1' in messages[0]
    assert not any(secret in messages[0] for secret in ('credential', 'private-response-body', '/private'))
    clock[0] += 61
    diagnostics.record(error, 'compiler')
    assert len(caplog.records) == 2 and 'failures=5' in caplog.records[-1].message
    diagnostics.record(OSError(errno.ENOENT, 'missing'), 'agent')
    assert len(caplog.records) == 2


def test_compiler_launch_failure_keeps_original_error_and_logs_capacity(tmp_path, monkeypatch, caplog):
    error = BlockingIOError(errno.EAGAIN, 'private-response-body', '/private/compiler')
    def launch(*args, **kwargs):raise error
    monkeypatch.setattr(compiler.spawner, 'Popen', launch)
    monkeypatch.setattr(compiler, '_wrap', lambda cfg, work, args: args)
    monkeypatch.setattr(compiler, '_SLOTS', SimpleNamespace(active=3, limit=4))
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    with pytest.raises(BlockingIOError) as caught:
        compiler._exec_driver(compiler.CompilerConfig([], sandbox='none'), tmp_path,
                              ['private-argument'], 1)
    assert caught.value is error
    assert any('lane=compiler' in r.message for r in caplog.records)
    assert 'private-argument' not in caplog.text and 'private-response-body' not in caplog.text


def test_agent_launch_failure_keeps_original_error_and_logs_capacity(tmp_path, monkeypatch, caplog):
    from decompnet.app import agents
    net, agent, fn = game(tmp_path)
    manager = object.__new__(Manager)
    manager.net, manager._down = net, False
    session = Session('1', 'claude', 'worker', 'game', 'worker', {})
    monkeypatch.setattr(net, 'authenticate', lambda token: agent)
    monkeypatch.setattr(manager, '_preflight_tools', lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, 'prepare_session', lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, '_command', lambda *args: (['private-argument'], {'API_KEY': 'private-key'}, 'private prompt'))
    error = BlockingIOError(errno.EAGAIN, 'private-response-body', '/private/agent')
    def launch(*args, **kwargs):raise error
    monkeypatch.setattr(agents.subprocess, 'Popen', launch)
    caplog.set_level(logging.WARNING, logger='decompdecomp')
    with pytest.raises(BlockingIOError) as caught:manager._run_cli(session, 'synthetic-token', tmp_path)
    assert caught.value is error
    assert any('lane=agent' in r.message for r in caplog.records)
    for secret in ('private-argument', 'private-key', 'private prompt', 'private-response-body'):
        assert secret not in caplog.text
