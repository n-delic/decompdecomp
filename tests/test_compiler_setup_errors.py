"""Missing compiler setup must reach every agent as an actionable error, without judging C."""
import json
from types import SimpleNamespace

import anyio
import pytest
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from decompnet import compile_configuration, compiler, network, toolchains
from decompnet.app import tools
from decompnet.network import Network, NetworkError
from tests.test_agent_workflow_efficiency import game


@pytest.mark.parametrize('transport', ['builtin', 'mcp'])
def test_setup_failure_reaches_agent_and_does_not_compile(tmp_path, monkeypatch, transport):
    net, agent, _ = game(tmp_path)
    project = {'id': 1, 'root': str(tmp_path), 'config': json.dumps({'compiler': {'toolchain': 'gcc-2.7.2-kmc'}})}
    message = "toolchain 'gcc-2.7.2-kmc' is not installed: install it in decompdecomp, under Compilers & tools"
    def resolve(*args):raise toolchains.ToolchainError(message)
    monkeypatch.setattr(compile_configuration, 'resolve', resolve)
    monkeypatch.setattr(network, 'compile_source', lambda *a: pytest.fail('Missing compiler must not compile or judge'))
    def compile_call(*args, **kwargs):return Network._compile(net, project, '', 'int f(void) { return 1; }')
    attempts = net.project_store('game').one('SELECT count(*) AS n FROM attempts')['n']
    records = []
    if transport == 'builtin':
        monkeypatch.setattr(tools, '_invoke', compile_call)
        assert tools.call(net, agent, 'game', 'compile_and_diff', {}) == 'error: ' + message
    else:
        monkeypatch.setattr(Context, 'headers', property(lambda self: {}))
        monkeypatch.setattr(tools, '_dispatch', compile_call)
        server = tools.build_mcp(net, lambda ctx: (agent, 'game'), lambda *args: records.append(args[-1]))
        async def run():return await server.call_tool('compile_and_diff', {'function': 'f', 'source': 'int f(void) { return 1; }'})
        with pytest.raises(ToolError, match='Compilers & tools') as failure:
            anyio.run(run)
        assert str(failure.value).endswith(message)
        assert not isinstance(failure.value, UnexpectedToolError)
        assert records[-1]['error'] == message
    assert net.project_store('game').one('SELECT count(*) AS n FROM attempts')['n'] == attempts


def test_failed_setup_is_not_a_cached_compile_result_and_recovers(tmp_path, monkeypatch):
    cfg = {'compiler': {'cmd': ['synthetic-cc']}}
    proj = {'id': 1, 'root': str(tmp_path), 'config': json.dumps(cfg)}
    calls = []
    original = toolchains.ToolchainError('Reinstall synthetic compiler in Compilers & tools')
    def resolve(*args):
        calls.append('resolve')
        if len(calls) == 1:raise original
        return compiler.CompilerConfig(['synthetic-cc'], sandbox='none')
    monkeypatch.setattr(compile_configuration, 'resolve', resolve)
    result = SimpleNamespace(language='c')
    monkeypatch.setattr(network, 'compile_source', lambda *args: result)
    with pytest.raises(NetworkError, match='Reinstall synthetic compiler') as failure:
        Network._compile(None, proj, '', 'int f(void) { return 1; }')
    assert failure.value.__cause__ is original
    assert Network._compile(None, proj, '', 'int f(void) { return 1; }') is result
    assert calls == ['resolve', 'resolve']
    assert result.effective_settings['compiler'] == cfg['compiler']


def test_unrelated_configuration_failure_remains_unexpected(tmp_path, monkeypatch):
    error = ValueError('synthetic configuration bug')
    def resolve(*args):raise error
    monkeypatch.setattr(compile_configuration, 'resolve', resolve)
    with pytest.raises(ValueError) as failure:
        Network._compile(None, {'root': str(tmp_path), 'config': '{}'}, '', '')
    assert failure.value is error
