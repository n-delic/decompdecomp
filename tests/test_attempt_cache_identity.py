"""An attempt's compile identity follows the compiler's settings, not every rewrite of the project config.

Rooms, saves, prepared evidence and auto-push rewrite `projects.config` for reasons no compiler sees. When the
identity held the whole config, each rewrite made identical attempts miss the session and persistent object caches.
"""
import json

import pytest

from decompnet import compile_sessions, compile_timing, toolchains
from decompnet.compiler import CompilerConfig, CompileResult
from tests.test_overlay_drafts import network  # noqa: F401


def identities(net, proj, fn, monkeypatch, *configs):
    from decompnet import network as core
    seen = []
    monkeypatch.setattr(core, 'compiler_config', lambda root, cfg: CompilerConfig(cmd=['cc', '{in}', '{out}']))
    def compile_source(invocation, header, source):
        seen.append(invocation.identity)
        return CompileResult(True, b'object', '')
    monkeypatch.setattr(core, 'compile_source', compile_source)
    for cfg in configs:
        net._compile({**proj, 'config': json.dumps(cfg)}, '', 'int f(void) { return 1; }', fn)
    return seen


def test_config_rewrites_the_compiler_never_sees_keep_the_identity(network, monkeypatch):  # noqa: F811
    net, proj, fn = network
    cfg = json.loads(proj['config'])
    first, second = identities(net, proj, fn, monkeypatch, cfg, {**cfg, 'auto_push': True, 'room': {'host': 'x'}})
    assert first is not None and first == second


def test_a_changed_compiler_setting_changes_the_identity(network, monkeypatch):  # noqa: F811
    net, proj, fn = network
    cfg = json.loads(proj['config'])
    other = {**cfg, 'compiler': {**cfg['compiler'], 'flags': [*cfg['compiler']['flags'], '-g']}}
    first, second = identities(net, proj, fn, monkeypatch, cfg, other)
    assert first != second


@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None, reason='needs pinned period compiler')
def test_identical_attempts_reuse_the_object_after_a_config_rewrite(network):  # noqa: F811
    net, proj, fn = network
    if toolchains.get('gcc-2.7.2-kmc').via_vm or toolchains.get('gcc-2.7.2-kmc').via_wsl:
        pytest.skip('the session cache does not cover remote runtimes')
    source = f'int {fn["name"]}(int x) {{ return x ? 1 : 2; }}'
    rewritten = {**proj, 'config': json.dumps({**json.loads(proj['config']), 'auto_push': True})}
    with compile_sessions.session():
        first = net._compile(proj, '', source, fn)
        hits = compile_timing.snapshot().get('cache_hits', 0)
        second = net._compile(rewritten, '', source, fn)
        assert compile_timing.snapshot().get('cache_hits', 0) == hits + 1
    assert first.ok and second.ok and first.obj == second.obj
