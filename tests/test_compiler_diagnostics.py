
from decompnet.app import compiler_diagnostics as diagnostics


def test_phase_summary_reports_removed_pseudos_without_claiming_spills(tmp_path):
    (tmp_path/'func.c.flow').write_text('Register 203 used 8 times across 19 insns; crosses 1 call; pointer.\n(reg/v:SI 203) (reg:SI 204)')
    (tmp_path/'func.c.combine').write_text('(reg:SI 204)')
    (tmp_path/'func.c.greg').write_text(';; 203 conflicts:\n;; Register 204 in 16.\n')
    (tmp_path/'ignore.txt').write_text('private other file')
    result=diagnostics.phase_summary(tmp_path)
    assert result['removed_between_flow_and_combine']==[203]
    assert result['phases']['greg']['empty_conflicts']==[203]
    assert result['phases']['flow']['register_count']==2
    assert result['phases']['flow']['lifetimes'][0]=={'register':203,'references':8,'live_insns':19,'calls_crossed':1,'pointer':True}
    assert all('ignore' not in p for p in result['phases'])


def test_uninstalled_compiler_never_runs_or_downloads(monkeypatch):
    monkeypatch.setattr(diagnostics.toolchains,'installed',lambda _:False)
    monkeypatch.setattr(diagnostics,'compile_source',lambda *a:(_ for _ in ()).throw(AssertionError()))
    assert 'already installed' in diagnostics.diagnose({'toolchain':'gcc-2.7.2-kmc'},'','int f(){}','f')['skipped']


def test_capture_callback_is_local_and_flags_are_not_mutated(monkeypatch,tmp_path):
    # A host compiler is only test infrastructure, never a matching toolchain.
    import shutil

    import pytest

    from decompnet.compiler import CompilerConfig, capture_artifacts, compile_source
    cc=shutil.which('cc')
    if not cc:pytest.skip('Host compiler unavailable')
    cfg=CompilerConfig([cc,'-c','{src}','-o','{obj}','-I{include}'],sandbox='none')
    seen=[]
    original=list(cfg.cmd)
    with capture_artifacts(lambda work:seen.append((work/'func.c').is_file())):
        assert compile_source(cfg,'','int f(void){return 1;}').ok
    assert compile_source(cfg,'','int f(void){return 2;}').ok
    assert seen==[True] and cfg.cmd==original


def test_legacy_weight_summary_exposes_reference_priority_and_loop_notes(tmp_path):
    (tmp_path/'func.c.flow').write_text('Register 201 used 22 times across 74 insns\nRegister 202 used 14 times across 38 insns\nNOTE_INSN_LOOP_BEG\n')
    phase=diagnostics.phase_summary(tmp_path)['phases']['flow']
    assert phase['loop_begin_notes']==1
    assert [r['register'] for r in phase['allocation_priority']]==[201,202]
    assert phase['allocation_priority'][0]['legacy_weight']==88/74
