"""Required app tools must load before Codex starts a paid model turn."""
import os
import sys
import tomllib

import pytest

from decompnet.agents import codex
from decompnet.app import agents
from tests.test_agents import URL, _game, _no_agent_settings


def overrides(command):
    return [command[i+1] for i,value in enumerate(command[:-1]) if value=='-c']


@pytest.mark.parametrize('focused',[False,True])
def test_codex_requires_enabled_app_transport_and_retains_token_environment(tmp_path,monkeypatch,focused):
    monkeypatch.setattr(codex,'codex_config',lambda:{'mcp_servers':{'decomp_agent':{'enabled':False},'other':{}}})
    if focused:
        command,env=codex.CODEX.focused_session_command('codex',tmp_path,URL,'private-token','gpt-6.1-sol','conversation')
    else:command,env=codex.CODEX.session_command('codex',tmp_path,URL,'private-token','gpt-6.1-sol')
    values=overrides(command)
    settings=tomllib.loads('\n'.join(values))['mcp_servers']
    assert settings['decomp_agent']['enabled'] is True
    assert settings['decomp_agent']['required'] is True
    assert settings['decomp_agent']['startup_timeout_sec']==30
    assert settings['decomp_agent']['tool_timeout_sec']==codex.TOOL_TIMEOUT
    assert settings['other']['enabled'] is False
    assert 'mcp_servers.decomp_agent.enabled=false' not in values
    assert env[codex.TOKEN_ENV]=='private-token' and 'private-token' not in ' '.join(command)
    if focused:assert command[-3:]==['resume','conversation','-']


@pytest.mark.parametrize('message',[
    'Error: required MCP servers failed to initialize: decomp_agent: connection refused',
    'required MCP server decomp_agent did not start: initialize timed out',
    'MCP startup failed: decomp_agent',
])
def test_codex_recognizes_pre_model_startup_failures(message):
    assert message in codex.CODEX.startup_failure(message)
    assert codex.CODEX.startup_failure('Provider usage limit reached') is None


def test_required_mcp_startup_error_is_visible_redacted_and_not_a_paid_handshake_retry(tmp_path,monkeypatch):
    _no_agent_settings(monkeypatch)
    net=_game(tmp_path);manager=agents.Manager(net,tmp_path,URL)
    s=manager._record('game','codex','codex-test','Codex',{})
    agent,token=net.local_agent(s.agent_name)
    monkeypatch.setattr(manager,'_preflight_tools',lambda *args, **kwargs:None)
    monkeypatch.setattr(manager,'prepare_session',lambda *args,**kwargs:None)
    error='Error: required MCP servers failed to initialize: decomp_agent: connection refused '+token
    script='import sys;sys.stdin.read();print('+repr(error)+',file=sys.stderr,flush=True);sys.exit(1)'
    monkeypatch.setattr(manager,'_command',lambda *args:([sys.executable,'-c',script],dict(os.environ),''))
    with pytest.raises(agents.ToolConnectionUnavailable,match='required app tools') as caught:
        manager._run_cli(s,token,tmp_path)
    assert 'connection refused' in str(caught.value)
    assert token not in str(caught.value) and '[redacted]' in str(caught.value)
    assert s.tool_handshake_failures==0 and not net.store.all('SELECT * FROM claims')
    assert s.usage['turns']==0 and s.usage['tokens']==0
    assert any(item['type']=='error' and 'connection refused' in item['text'] for item in s.feed)


def test_unknown_cli_failure_retains_redacted_diagnostics_in_handshake_error(tmp_path,monkeypatch):
    _no_agent_settings(monkeypatch)
    net=_game(tmp_path);manager=agents.Manager(net,tmp_path,URL)
    s=manager._record('game','codex','codex-test','Codex',{})
    _,token=net.local_agent(s.agent_name)
    monkeypatch.setattr(manager,'_preflight_tools',lambda *args, **kwargs:None)
    monkeypatch.setattr(manager,'prepare_session',lambda *args,**kwargs:None)
    script='import sys;sys.stdin.read();print('+repr('unrecognized startup problem '+token)+',file=sys.stderr);sys.exit(1)'
    monkeypatch.setattr(manager,'_command',lambda *args:([sys.executable,'-c',script],dict(os.environ),''))
    with pytest.raises(agents.ToolConnectionUnavailable) as caught:manager._run_cli(s,token,tmp_path)
    assert 'unrecognized startup problem' in str(caught.value) and token not in str(caught.value)
    assert s.tool_handshake_failures==1
