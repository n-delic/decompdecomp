"""Cursor workers isolate MCP/settings and never invent token or billing totals."""
import json
from pathlib import Path

from decompnet.agents.cursor import CURSOR


def test_cursor_private_home_and_permissions_do_not_import_user_mcp(tmp_path, monkeypatch):
    original = tmp_path / 'user'
    config = original / '.cursor'
    config.mkdir(parents=True)
    (config / 'mcp.json').write_text('{"mcpServers":{"personal":{"command":"must-not-run"}}}')
    (config / 'cli-config.json').write_text('{"permissions":{"allow":["Shell(*)"]},"network":{"useHttp1ForAgent":true}}')
    (config / 'auth.json').write_text('{"accessToken":"login-material"}')
    monkeypatch.setenv('HOME', str(original))
    monkeypatch.setenv('USERPROFILE', str(original))
    monkeypatch.delenv('CURSOR_CONFIG_DIR', raising=False)
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    work = tmp_path / 'worker'
    work.mkdir()
    cmd, env = CURSOR.session_command('cursor-agent', work, 'http://127.0.0.1:123/mcp', 'private-token', 'auto')
    private = Path(env['CURSOR_CONFIG_DIR'])
    assert env['HOME'] != str(original) and '--force' not in cmd and '--approve-mcps' in cmd
    assert 'private-token' not in ' '.join(cmd)
    servers = json.loads((private / 'mcp.json').read_text())['mcpServers']
    assert list(servers) == ['decomp']
    assert servers['decomp']['headers']['Authorization'] == 'Bearer private-token'
    settings = json.loads((private / 'cli-config.json').read_text())
    assert settings['permissions']['allow'] == ['Mcp(decomp:*)']
    assert {'Shell(*)', 'Read(/**)', 'Write(/**)', 'WebFetch(*)'} <= set(settings['permissions']['deny'])
    assert settings['network']['useHttp1ForAgent'] is True
    assert json.loads((private / 'auth.json').read_text())['accessToken'] == 'login-material'
    assert 'personal' in (config / 'mcp.json').read_text()


def test_cursor_model_ids_are_preserved_without_guessing_effort():
    rows = CURSOR.read_models(['Available models', 'auto - Auto (current, default)',
                               'claude-sonnet-5-thinking-high - Sonnet Thinking', 'Tip: use --model'])
    assert [r['id'] for r in rows] == ['auto', 'claude-sonnet-5-thinking-high']
    assert rows[0]['name'] == 'Auto' and not CURSOR.takes_effort


def test_cursor_stream_events_and_missing_usage():
    state = {}
    assert CURSOR.parse({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Working'}]}}, state) == [('text', 'Working')]
    rows = CURSOR.parse({'type': 'tool_call', 'subtype': 'started', 'tool_call': {'mcpToolCall': {
        'args': {'providerIdentifier': 'decomp', 'toolName': 'get_function', 'args': {'function': 'probe'}}}}}, state)
    assert rows[0][0] == 'tool' and 'get_function' in rows[0][1]
    assert CURSOR.parse({'type': 'tool_call', 'subtype': 'completed'}, state) == []
    rows = CURSOR.parse({'type': 'result', 'result': 'Working'}, state)
    assert [kind for kind, _ in rows] == ['info']
    assert CURSOR.estimate_cost('auto', {}) is None
    assert CURSOR.fatal('Authentication required')
    assert not CURSOR.fatal('temporary network error')


def test_cursor_explicit_usage_is_tracked_only_when_present():
    rows = CURSOR.parse({'type': 'result', 'usage': {'input_tokens': 123, 'output_tokens': 7}}, {})
    usage = json.loads(next(text for kind, text in rows if kind == 'usage'))
    assert usage['input'] == 123 and usage['output'] == 7


def test_current_cursor_camelcase_usage_excludes_cache_already():
    rows = CURSOR.parse({'type': 'result', 'usage': {'inputTokens': 8751, 'outputTokens': 171,
                        'cacheReadTokens': 7680, 'cacheWriteTokens': 0}}, {})
    usage = json.loads(next(text for kind, text in rows if kind == 'usage'))
    assert usage == {'input': 8751, 'output': 171, 'cache_read': 7680}


def test_mac_login_token_is_only_in_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.agents import cursor

    monkeypatch.setattr(cursor.sys, 'platform', 'darwin')
    monkeypatch.delenv('CURSOR_AUTH_TOKEN', raising=False)
    monkeypatch.delenv('CURSOR_API_KEY', raising=False)
    monkeypatch.setattr(cursor.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='login-secret\n'))
    cmd, env = CURSOR.session_command('cursor-agent', tmp_path, 'http://127.0.0.1:1/mcp', 'mcp-secret', 'auto')
    assert env['CURSOR_AUTH_TOKEN'] == 'login-secret' and env['AGENT_CLI_CREDENTIAL_STORE'] == 'memory'
    assert 'login-secret' not in ' '.join(cmd)
    assert 'login-secret' not in (Path(env['CURSOR_CONFIG_DIR']) / 'cli-config.json').read_text()


def test_cursor_thinking_deltas_are_coalesced():
    state = {}
    assert CURSOR.parse({'type':'thinking','subtype':'delta','text':'Inspecting '},state) == []
    assert CURSOR.parse({'type':'thinking','subtype':'delta','text':'assembly'},state) == []
    assert CURSOR.parse({'type':'thinking','subtype':'completed'},state) == [('think','Inspecting assembly')]
    assert not state


def test_cursor_platform_launchers(monkeypatch):
    from decompnet.agents import cursor

    monkeypatch.setattr(cursor.sys, 'platform', 'win32')
    assert CURSOR.command == 'agent'
    monkeypatch.setattr(cursor.sys, 'platform', 'linux')
    assert CURSOR.command == 'cursor-agent'
