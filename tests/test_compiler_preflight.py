"""Warning diagnostics never supply a judge score or change configured flags."""
import json
from types import SimpleNamespace

from decompnet import toolchains
from decompnet.app import compiler_preflight


def test_diagnostics_are_advisory_cached_and_invalidated_by_actual_inputs(monkeypatch):
    monkeypatch.setattr(toolchains, 'diagnostic_flags', lambda tid: ('-Wall', '-Wuninitialized'))
    calls = []
    header = ['typedef int word;']
    settings = {'toolchain':'known', 'flags':['-O2']}
    proj = {'id':1, 'slug':'game', 'root':'/scratch', 'config':json.dumps({'compiler':settings})}
    fn = {'best_source':'int f(void) { int *p; return *p; }', 'vram':16, 'unit':''}
    def compile(project, context, source, function):
        calls.append(json.loads(project['config']))
        return SimpleNamespace(ok=True, log="func.c:1: warning: 'p' might be used uninitialized", obj=b'unused')
    net = SimpleNamespace(cfg=lambda p:json.loads(p['config']), is_rom=lambda p:True, fork_slug=lambda s:None,
                          _context=lambda *a:{'header':header[0]}, _compile=compile)
    assert 'uninitialized' in compiler_preflight.warnings(net, proj, fn)
    compiler_preflight.warnings(net, proj, fn)
    assert len(calls) == 1
    assert calls[0]['compiler']['flags'] == ['-O2','-Wall','-Wuninitialized']
    assert json.loads(proj['config'])['compiler'] == settings
    header[0] += '\nextern int data;'
    compiler_preflight.warnings(net, proj, fn)
    assert len(calls) == 2
    fn['best_source'] += ' '
    compiler_preflight.warnings(net, proj, fn)
    assert len(calls) == 3
    net.fork_slug = lambda s:'fork'
    assert not compiler_preflight.warnings(net, proj, fn)
    assert len(calls) == 3


def test_warning_flags_do_not_install_unknown_or_non_gcc_tools():
    assert toolchains.diagnostic_flags('gcc-2.7.2-kmc') == ('-Wall', '-Wuninitialized')
    assert not toolchains.diagnostic_flags('ido-5.3')
    assert not toolchains.diagnostic_flags('unknown')


def test_failed_diagnostic_build_cannot_supply_a_warning_or_score(monkeypatch):
    monkeypatch.setattr(toolchains, 'diagnostic_flags', lambda tid: ('-Wall',))
    proj = {'id':1,'slug':'game','root':'/scratch','config':json.dumps({'compiler':{'toolchain':'example','flags':[]}})}
    net = SimpleNamespace(cfg=lambda p:json.loads(p['config']), is_rom=lambda p:True, fork_slug=lambda s:None,
                          _context=lambda *a:{'header':''},
                          _compile=lambda *a:SimpleNamespace(ok=False, log='warning: not supported', obj=None))
    fn = {'best_source':'int f(void){return 0;}','unit':'','vram':0}
    assert compiler_preflight.warnings(net, proj, fn) == ''
