"""Initial C keeps exact call identities and never compiles m2c's failure placeholders."""
import subprocess
import threading
from types import SimpleNamespace

import pytest

from decompnet import seeds
from decompnet.app import draft_regeneration, draft_reports, initial_draft_repairs
from decompnet.rom.symbols import Symbol, SymbolTable

from .test_overlay_drafts import network  # noqa: F401


def test_call_names_do_not_become_interior_data_or_function_addresses(network):  # noqa: F811
    net, proj, _ = network
    net._syms[proj['id']] = SymbolTable([
        Symbol('containing_function', 0x80002000, 0x100, 'func'),
        Symbol('data_alias', 0x80003000, 4, 'data'),
        Symbol('renamed_callee', 0x80004000, 16, 'func'),
    ])
    text = 'jal func_80002010\njal func_80003000\njal func_80004000\nlui $v0, %hi(D_80002010)'
    renamed = net.rename_tokens(proj, text)
    assert net._current_names(proj, {'func_80002010', 'func_80004000'}) == {'func_80002010', 'renamed_callee'}
    assert renamed == ('jal func_80002010\njal func_80003000\njal renamed_callee\n'
                       'lui $v0, %hi(containing_function+0x10)')


@pytest.mark.parametrize('output', [
    'Bad function call operand containing+0x10\nvoid f(void) {}',
    'void f(void) {\n    // Error: unsupported instruction\n}',
    'int f(void) { return M2C_ERROR(); }',
    '',
])
@pytest.mark.parametrize('strict', [True, False])
def test_success_exit_with_partial_or_empty_output_is_a_generation_failure(monkeypatch, output, strict):
    monkeypatch.setattr(seeds, 'available', lambda: True)
    monkeypatch.setattr(seeds.subprocess, 'run', lambda argv, **kw: subprocess.CompletedProcess(argv, 0, output, ''))
    if strict:
        with pytest.raises(seeds.DraftError):
            seeds.run_m2c('glabel f\njr $ra\nnop', 'f', 'mips-gcc-c', valid=True, strict=True)
    else:
        assert seeds.run_m2c('glabel f\njr $ra\nnop', 'f', 'mips-gcc-c', valid=True) is None


def test_failed_generation_is_retained_as_a_reason_not_c(network, monkeypatch):  # noqa: F811
    net, proj, fn = network
    monkeypatch.setattr(seeds, 'available', lambda: True)
    def fail(*args, **kwargs):
        raise seeds.DraftError('Missing delay slot; review the function boundary.')
    monkeypatch.setattr(seeds, 'generate_from_asm', fail)
    assert net._seed_for(fn, proj) == ''
    report = draft_reports.read(net, fn['id'])
    assert 'Missing delay slot' in report['failures'][-1]['reason']
    assert not report['compiled']


def test_regeneration_retains_first_compiling_private_repair_and_releases_claim(network, monkeypatch):  # noqa: F811
    net, proj, fn = network
    agent, _ = net.local_agent('draft-test')
    compiled = []
    def compile_source(proj, header, source, fn):
        compiled.append(source)
        return SimpleNamespace(ok=source.endswith('repaired C'), obj=b'obj', log='invalid callback type')
    monkeypatch.setattr(net, '_compile', compile_source)
    monkeypatch.setattr(net, '_evaluate', lambda *args: (None, None, []))
    def repairs(*args):
        yield 'initial_callback_context', 'repaired C'
        pytest.fail('Stop at the first compiling baseline')
    monkeypatch.setattr(initial_draft_repairs, 'recovery_drafts', repairs)
    outcome, report = draft_regeneration.trial(net, agent, proj, fn['id'], 'raw C', False)
    assert outcome == 'fail'  # Compiling alone cannot establish a byte match.
    assert report['compiled'] and not report['matched'] and report['trials'] == 2
    assert report['family'] == 'initial_callback_context'
    assert report['compiled_source'].endswith('repaired C')
    assert draft_regeneration.has_compiling_c(net, net.store.one('SELECT * FROM functions WHERE id=?', fn['id']))
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id'])


def test_regeneration_caps_repairs_and_checks_stop_between_compiles(network, monkeypatch):  # noqa: F811
    net, proj, fn = network
    agent, _ = net.local_agent('draft-test')
    compiled = []
    monkeypatch.setattr(net, '_compile', lambda *args: compiled.append(args[2]) or SimpleNamespace(ok=False, log='bad C'))
    monkeypatch.setattr(initial_draft_repairs, 'recovery_drafts', lambda *args: ((str(i), 'repair '+str(i)) for i in range(30)))
    _, report = draft_regeneration.trial(net, agent, proj, fn['id'], 'raw C', False)
    assert report['trials'] == len(compiled) == 7
    stop = threading.Event()
    def cancelled(*args):
        stop.set()
        return SimpleNamespace(ok=False, log='bad C')
    monkeypatch.setattr(net, '_compile', cancelled)
    _, report = draft_regeneration.trial(net, agent, proj, fn['id'], 'raw C', False, stop)
    assert report['trials'] == 1


@pytest.mark.skipif(not seeds.available(), reason='needs pinned m2c')
def test_exact_call_name_generates_compiling_c_with_the_period_compiler(network):  # noqa: F811
    from decompnet import toolchains
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned period compiler')
    net, proj, fn = network
    net.symbols(proj['id']).add(Symbol('containing_function', 0x80002000, 0x100, 'func'))
    asm = ('glabel '+fn['name']+'\naddiu $sp, $sp, -24\nsw $ra, 20($sp)\n'
           'jal func_80002010\nnop\nlw $ra, 20($sp)\njr $ra\naddiu $sp, $sp, 24\n')
    with pytest.raises(seeds.DraftError, match='Bad function call operand'):
        seeds.run_m2c(asm.replace('func_80002010', 'containing_function+0x10'), fn['name'],
                      'mips-gcc-c', valid=True, strict=True)
    source = seeds.run_m2c(net.rename_tokens(proj, asm), fn['name'], 'mips-gcc-c', valid=True, strict=True)
    assert source and 'M2C_ERROR(' not in source
    built = net._compile(proj, net._context(proj['id'], fn['unit'])['header'], seeds.M2C_MACROS+'\n'+source, fn)
    assert built.ok, built.log


def test_explanatory_redrafts_can_still_show_unset_registers(monkeypatch):
    output = 'int f(void) { return M2C_ERROR(/* Read from unset register $a1 */); }'
    monkeypatch.setattr(seeds, 'available', lambda: True)
    monkeypatch.setattr(seeds.subprocess, 'run', lambda argv, **kw: subprocess.CompletedProcess(argv, 0, output, ''))
    assert seeds.run_m2c('glabel f', 'f', 'mips-gcc-c', strict=True) == output
