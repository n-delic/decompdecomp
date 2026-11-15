"""The real pinned native compiler can reuse invocation setup without changing its objects."""
import pytest

from decompnet import compile_sessions, toolchains
from tests.test_overlay_drafts import network  # noqa: F401


@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None,reason='needs pinned period compiler')
def test_network_compile_reuses_native_setup_and_keeps_verdict_inputs(network,monkeypatch):  # noqa: F811
    from decompnet import network as core
    net,proj,fn=network;calls=[];original=core.compiler_config
    if toolchains.get('gcc-2.7.2-kmc').via_vm or toolchains.get('gcc-2.7.2-kmc').via_wsl:
        pytest.skip('native setup reuse does not replace remote runtime preparation')
    def build(*args):calls.append(args);return original(*args)
    monkeypatch.setattr(core,'compiler_config',build)
    source=f'int {fn["name"]}(int x) {{ return x ? 1 : 2; }}'
    with compile_sessions.session():
        first=net._compile(proj,'',source,fn)
        second=net._compile(proj,'',source,fn)
        assert first.ok and second.ok and first.obj==second.obj
        assert first.effective_settings==second.effective_settings and len(calls)==1
