"""Retain a real CLI MCP failure even when later permission noise fills stderr."""
import json
import os
import sys

import pytest

from decompnet.agents.opencode import OPENCODE
from decompnet.app import agents
from tests.test_output_limit_recovery import cli

WARNING = ('timestamp=2026-01-01T00:00:00Z level=WARN run=synthetic '
           'message="server unavailable" key=decompdecomp type=remote status=failed')


def test_warning_is_a_fixed_diagnostic_not_a_provider_rejection():
    result = OPENCODE.stderr_connection(WARNING+' url=https://private.invalid/?token=secret error="private body"')
    assert 'MCP server was unavailable' in result
    assert all(text not in result for text in ('private', 'secret', 'synthetic', 'https:'))
    assert not OPENCODE.clean_stderr(WARNING)
    assert not OPENCODE.stderr_failure(WARNING, 'opencode/example-free')
    assert not OPENCODE.stderr_retry(WARNING, 'opencode/example-free')


@pytest.mark.parametrize('line', [
    WARNING.replace('key=decompdecomp', 'key=other'),
    WARNING.replace('status=failed', 'status=connected'),
    WARNING.replace('type=remote', 'type=local'),
    WARNING.replace('message="server unavailable"', 'message="quoted server unavailable"'),
    WARNING.replace('level=WARN', 'level=INFO'),
    'level=ERROR message="provider failed" error="'+WARNING+'"',
    'level=WARN message="server unavailable key=decompdecomp type=remote status=failed"',
])
def test_unrelated_or_quoted_warnings_are_not_connection_evidence(line):
    assert not OPENCODE.stderr_connection(line)


@pytest.mark.parametrize('recovered', [False, True])
def test_real_child_persists_one_warning_and_preserves_handshake_policy(tmp_path, monkeypatch, caplog, recovered):
    caplog.set_level('INFO', logger='decompdecomp')
    net, manager, session, agent, token, claim = cli(tmp_path, monkeypatch, {}, provider='opencode')
    pending = net.__dict__.setdefault('_pending_tool_handshakes', set())
    pending.add((agent.id, session.slug))
    before = dict(net.store.one('SELECT * FROM functions WHERE id=?', claim['id']))
    records = []
    session.record = lambda kind, text: records.append((kind, text))

    def command(*args):
        if recovered:pending.discard((agent.id, session.slug))
        # An actual child can warn, then recover or keep making unrelated turns.
        # A long stderr tail must not erase the earlier connection evidence.
        script = ('import sys,json\nsys.stdin.read()\n'
                  + ''.join('print('+repr(line)+',file=sys.stderr,flush=True)\n' for line in
                            [WARNING+' token=never-log-this']*30 + ['unrelated permission denial']*30)
                  + 'print('+repr(json.dumps({'type':'text','part':{'text':'synthetic complete reply'}}))+',flush=True)\n')
        return [sys.executable, '-c', script], dict(os.environ), 'synthetic prompt'

    monkeypatch.setattr(manager, '_command', command)
    if recovered:
        assert manager._run_cli(session, token, tmp_path) is True
        assert session.tool_handshake_failures == 0
    else:
        with pytest.raises(agents.ToolConnectionUnavailable, match='MCP server was unavailable'):
            manager._run_cli(session, token, tmp_path)
        assert session.tool_handshake_failures == 1
        with pytest.raises(agents.Fatal, match='Automatic retries paused after two attempts'):
            manager._run_cli(session, token, tmp_path)
        assert session.tool_handshake_failures == 2
    diagnostics = [text for kind, text in records if kind == 'info' and 'CLI tool connection diagnostic:' in text]
    assert len(diagnostics) == (1 if recovered else 2)  # once per CLI, despite repeated warnings
    assert 'MCP server was unavailable' in caplog.text
    assert 'never-log-this' not in caplog.text and 'never-log-this' not in str(records)
    assert not session.limited and not session.auth_failed and not session.throttled
    assert dict(net.store.one('SELECT * FROM functions WHERE id=?', claim['id'])) == before
    assert net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?', claim['id'], agent.id)
