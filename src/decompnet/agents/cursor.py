"""Cursor CLI: private HOME/configuration, allowlisted MCP tools, streamed headless sessions.

Cursor reads MCP configuration from HOME as well as CURSOR_CONFIG_DIR, so both
must be isolated. macOS passes its login token from Keychain through the
environment; file-backed logins on other hosts are copied into the private home
without importing MCP settings.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import SAID_CHARS, Agent, args_brief, error_text, number, read_json, tool_name, usage_item


def config_home() -> Path:
    return Path(os.environ.get('CURSOR_CONFIG_DIR') or
                (Path(os.environ['XDG_CONFIG_HOME']) / 'cursor' if os.environ.get('XDG_CONFIG_HOME')
                 else Path.home() / '.cursor'))


class Cursor(Agent):
    id = 'cursor-cli'
    name = 'Cursor Agent'

    @property
    def command(self):
        return 'agent' if sys.platform == 'win32' else 'cursor-agent'

    vendor = 'Cursor'
    install = 'curl https://cursor.com/install -fsS | bash'
    windows_install = "irm 'https://cursor.com/install?win32=true' | iex"
    signin = 'Run `agent login` in Terminal, then start this agent again.'
    docs = 'https://cursor.com/docs/cli/overview'
    models_args = ('models',)

    def config_model(self):
        model = read_json(config_home() / 'cli-config.json').get('model')
        return model.get('modelId') if isinstance(model, dict) else model if isinstance(model, str) else None

    def read_models(self, lines):
        out = []
        for line in lines:
            line = re.sub(r'\x1b\[[0-9;]*m', '', line)
            match = re.match(r'^([\w][\w.\[\]=,-]*) - (.+)$', line)
            if match:
                model, label = match.groups()
                out.append({'id': model, 'name': re.sub(r' \((?:current, )?default\)$', '', label),
                            'note': 'from your Cursor account'})
        return out

    def session_command(self, exe, workdir, mcp_url, token, model):
        home = workdir / '.cursor-home'
        private = home / '.cursor'
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        home.chmod(0o700)
        # Only login material is carried over, never global rules, plugins or MCP servers.
        login_files = [(Path.home() / relative, relative) for relative in [
                         '.cursor/auth.json', '.cursor-agent/auth.json', '.config/cursor/auth.json',
                         '.config/cursor-agent/auth.json', 'AppData/Roaming/Cursor/auth.json']]
        if os.environ.get('APPDATA'):
            login_files.append((Path(os.environ['APPDATA']) / 'Cursor/auth.json', 'AppData/Roaming/Cursor/auth.json'))
        if os.environ.get('XDG_CONFIG_HOME'):
            login_files.append((Path(os.environ['XDG_CONFIG_HOME']) / 'cursor/auth.json', '.config/cursor/auth.json'))
        for source, relative in login_files:
            if source.is_file():
                target = home / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                target.chmod(0o600)
        settings = {'version': 1, 'editor': {'vimMode': False}, 'approvalMode': 'allowlist',
                    'permissions': {'allow': ['Mcp(decomp:*)'],
                                    'deny': ['Shell(*)', 'Read(**)', 'Read(/**)', 'Write(**)', 'Write(/**)',
                                             'WebFetch(*)']}, 'notifications': False}
        network = read_json(config_home() / 'cli-config.json').get('network')
        if isinstance(network, dict):
            settings['network'] = {k: v for k, v in network.items() if k == 'useHttp1ForAgent' and isinstance(v, bool)}
        for name, data in [('cli-config.json', settings), ('mcp.json', {'mcpServers': {'decomp': {
                'url': mcp_url, 'headers': {'Authorization': f'Bearer {token}'}}}})]:
            path = private / name
            path.write_text(json.dumps(data), encoding='utf-8')
            path.chmod(0o600)
        cmd = [exe, '-p', '--output-format', 'stream-json', '--workspace', str(workdir),
               '--trust', '--approve-mcps']
        if model:
            cmd += ['--model', model]
        env = {'HOME': str(home), 'USERPROFILE': str(home), 'APPDATA': str(home / 'AppData/Roaming'),
                     'XDG_CONFIG_HOME': str(home / '.config'), 'CURSOR_CONFIG_DIR': str(private),
                     'CURSOR_DATA_DIR': str(private), 'AGENT_CLI_CREDENTIAL_STORE': 'file', 'DIRENV_DISABLE': '1'}
        if sys.platform == 'darwin' and not (os.environ.get('CURSOR_API_KEY') or os.environ.get('CURSOR_AUTH_TOKEN')):
            # The login keychain lookup relies on the real HOME. Give the isolated CLI
            # only its access token through its supported environment variable.
            try:
                result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', 'cursor-access-token',
                                         '-a', 'cursor-user', '-w'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    env['CURSOR_AUTH_TOKEN'] = result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        if env.get('CURSOR_AUTH_TOKEN') or os.environ.get('CURSOR_AUTH_TOKEN') or os.environ.get('CURSOR_API_KEY'):
            env['AGENT_CLI_CREDENTIAL_STORE'] = 'memory'
        return cmd, env

    def fatal(self, error):
        return bool(re.search(r"authentication required|not authenticated|invalid model|unknown model|unknown option|couldn't access.*keychain", error, re.I))

    def investigation_command(self, exe, prompt):
        return [exe, prompt]

    def parse(self, event, state):
        kind = event.get('type')
        if kind == 'thinking':
            if event.get('subtype') == 'delta':
                state['thinking'] = (state.get('thinking', '') + str(event.get('text') or ''))[:SAID_CHARS]
            elif event.get('subtype') == 'completed':
                text = state.pop('thinking', '')
                return [('think', text)] if text else []
            return []
        if kind == 'assistant':
            return [('text', str(c['text']).strip()) for c in (event.get('message') or {}).get('content', [])
                    if isinstance(c, dict) and c.get('type') == 'text' and c.get('text')]
        if kind == 'tool_call' and event.get('subtype') == 'started':
            call = event.get('tool_call') or {}
            for name, value in call.items():
                if not isinstance(value, dict):
                    continue
                args = value.get('args') or {}
                tool = args.get('toolName') or args.get('name') or name
                return [('tool', f'{tool_name(tool)}({args_brief(args.get("args") or args)})')]
        if kind == 'error' or event.get('is_error'):
            return [('error', error_text(event.get('error') or event.get('result') or event.get('message'))[:SAID_CHARS])]
        if kind == 'result':
            out = [('info', 'Cursor session finished')]
            usage = event.get('usage')
            # Current CLI emits camelCase; its inputTokens already excludes cache
            # reads/writes (verified against the installed CLI's turnEnded conversion).
            if isinstance(usage, dict) and any(k in usage for k in ('inputTokens', 'outputTokens')):
                out.append(usage_item(input=number(usage, 'inputTokens'), output=number(usage, 'outputTokens'),
                                      cache_read=number(usage, 'cacheReadTokens'),
                                      cache_write=number(usage, 'cacheWriteTokens')))
            elif isinstance(usage, dict) and any(k in usage for k in ('input_tokens', 'output_tokens', 'total_tokens')):
                out.append(usage_item(input=number(usage, 'input_tokens'), output=number(usage, 'output_tokens'),
                                      tokens=number(usage, 'total_tokens')))
            return out
        return []


CURSOR = Cursor()
