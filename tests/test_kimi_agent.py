"""Kimi's private home and stdin bridge keep credentials, prompts and tools scoped to the helper."""
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from decompnet.agents.kimi import KIMI, TOKEN_ENV


def test_private_config_preserves_login_but_excludes_user_tools_hooks_and_plugins(tmp_path, monkeypatch):
    home = tmp_path / 'user-kimi'
    home.mkdir()
    (home / 'oauth').mkdir()
    (home / 'oauth/login').write_text('credential')
    original = '''default_model = "kimi-code/test"
[providers."managed:kimi-code"]
type = "kimi"
oauth = {storage="file", key="oauth/login"}
[models."kimi-code/test"]
provider = "managed:kimi-code"
model = "test"
max_context_size = 262144
[[hooks]]
event = "PreToolUse"
command = "do-not-run"
[tools]
enabled = ["Bash"]
'''
    (home / 'config.toml').write_text(original)
    (home / 'mcp.json').write_text('{"mcpServers":{"unrelated":{"command":"do-not-run"}}}')
    monkeypatch.setenv('KIMI_CODE_HOME', str(home))
    work = tmp_path / 'work'
    work.mkdir()
    cmd, env = KIMI.session_command('kimi', work, 'http://127.0.0.1:1/mcp', 'bearer-secret', 'kimi-code/test#low')
    assert cmd[-2:] == ['-m', 'kimi-code/test'] and all('bearer-secret' not in arg for arg in cmd)
    private = Path(env['KIMI_CODE_HOME'])
    config = tomllib.loads((private / 'config.toml').read_text())
    assert config['tools']['enabled'] == ['mcp__decomp__*']
    assert config['thinking']['effort'] == 'low'
    assert 'hooks' not in config and 'plugins' not in config
    profile = (private / 'decomp-agent.md').read_text()
    assert 'tools: ["mcp__decomp__*"]' in profile and 'subagents: []' in profile
    assert (private / 'oauth/login').read_text() == 'credential'
    assert env[TOKEN_ENV] == 'bearer-secret'
    assert json.loads((private / 'mcp.json').read_text()) == {'mcpServers': {'decomp': {
        'url': 'http://127.0.0.1:1/mcp', 'bearerTokenEnvVar': TOKEN_ENV, 'toolTimeoutMs': 180000}}}
    assert (home / 'config.toml').read_text() == original
    if os.name != 'nt':
        assert (private / 'config.toml').stat().st_mode & 0o777 == 0o600
    assert KIMI.config_model() == 'kimi-code/test'
    assert KIMI.models(None, run=False)[0]['id'] == 'kimi-code/test'


@pytest.mark.parametrize('newline',['\n','\r\n'])
def test_stdin_bridge_passes_prompt_in_memory_not_process_argv(tmp_path,newline):
    node = shutil.which('node')
    if not node:
        pytest.skip('needs Node')
    entry = tmp_path / 'fake-cli.mjs'
    entry.write_text('console.log(JSON.stringify({args:process.argv.slice(2)}));')
    cmd = KIMI.launch_command([node, str(entry), '--output-format', 'stream-json'])
    prompt = 'Multiline prompt'+newline+'with "quotes" and `shell` syntax $(untouched)'
    assert all(prompt not in arg for arg in cmd)
    result = subprocess.run(cmd, input=prompt.encode(), capture_output=True, check=True, timeout=10)
    assert json.loads(result.stdout)['args'] == ['--output-format', 'stream-json', '-p', prompt]


def test_stream_messages_and_usage_do_not_count_context_as_spend():
    event = {'role': 'assistant', 'content': 'Done', 'tool_calls': [{'function': {
        'name': 'mcp__decomp__claim_function', 'arguments': '{"count":1}'}}]}
    parsed = KIMI.parse(event, {})
    assert parsed[0] == ('text', 'Done') and parsed[1][0] == 'tool'
    assert KIMI.parse({'sessionUpdate': 'usage_update', 'used': 20000, 'size': 262144}, {}) == []
    assert KIMI.parse({'error': 'login required'}, {}) == [('error', 'login required')]


def test_usage_bridge_reads_only_new_records_and_ignores_duplicate_step_events(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('needs Node')
    home = tmp_path / 'private'
    wire = home / 'sessions/work/session/agents/main/wire.jsonl'
    wire.parent.mkdir(parents=True)
    event = {'type': 'usage.record', 'agentId': 'main', 'usageScope': 'turn',
             'usage': {'inputOther': 10, 'output': 4, 'inputCacheRead': 30, 'inputCacheCreation': 2}}
    wire.write_text(json.dumps(event) + '\n')
    entry = tmp_path / 'fake.mjs'
    entry.write_text('import fs from "node:fs";const file=' + json.dumps(str(wire)) + ';' +
                     'fs.appendFileSync(file,' + json.dumps(json.dumps(event) + '\n' +
                         json.dumps({'type': 'context.append_loop_event', 'usage': event['usage']}) + '\n') + ');')
    cmd = KIMI.launch_command([node, str(entry)])
    result = subprocess.run(cmd, input='test', env=dict(os.environ, KIMI_CODE_HOME=str(home)),
                            text=True, capture_output=True, check=True, timeout=10)
    parsed = KIMI.parse(json.loads(result.stdout), {})
    assert json.loads(parsed[0][1]) == {'input': 10, 'output': 4, 'cache_read': 30, 'cache_write': 2, 'turns': 1}


def test_legacy_kimi_launcher_has_an_actionable_error(tmp_path):
    old = tmp_path / 'kimi'
    old.write_text('#!/usr/bin/env python3')
    with pytest.raises(RuntimeError, match='legacy Python'):
        KIMI.launch_command([str(old)])


def test_node_prefixed_npm_symlink_resolves_before_entry_validation(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('needs Node')
    entry = tmp_path / 'main.mjs'
    entry.write_text('console.log(JSON.stringify({args:process.argv.slice(2)}));')
    shim = tmp_path / 'kimi'
    try:
        shim.symlink_to(entry)
    except OSError:
        pytest.skip('host cannot create symlinks')
    cmd = KIMI.launch_command([node, str(shim), '--output-format', 'stream-json'])
    assert str(entry.resolve()) in cmd
    result = subprocess.run(cmd, input='prompt through npm shim', text=True, capture_output=True, check=True, timeout=10)
    assert json.loads(result.stdout)['args'][-2:] == ['-p', 'prompt through npm shim']
