
from decompnet.app import compiler_diagnostics as diagnostics


def test_unknown_output_format_has_no_equivalence_fingerprint():
    assert diagnostics.output_fingerprint(b'not an ELF object') is None


def test_output_fingerprint_includes_named_relocations_and_data(monkeypatch):
    class Section(dict):
        name='.data'
        def data(self):return self['data']
    class Relocations(dict):
        def iter_relocations(self):return iter(self['relocations'])
    class Relocation(dict):
        @property
        def entry(self):return self
    class Symbol:
        def __init__(self,name):self.name=name
    from types import SimpleNamespace
    data=Section(sh_flags=2,sh_size=4,sh_type='SHT_PROGBITS',data=b'abcd')
    bss=Section(sh_flags=2,sh_size=8,sh_type='SHT_NOBITS');bss.name='.bss'
    relocation=Relocation(r_offset=0,r_info_type=1,r_info_sym=1,r_addend=0)
    relocs=Relocations(sh_flags=0,sh_link=1,sh_info=2,relocations=[relocation])
    symbol=Symbol('first')
    elf=SimpleNamespace(iter_sections=lambda:iter([data,bss,relocs]),
                        get_section=lambda i:SimpleNamespace(get_symbol=lambda _:symbol) if i==1 else data)
    monkeypatch.setattr(diagnostics,'ELFFile',lambda _:elf)
    monkeypatch.setattr(diagnostics,'RelocationSection',Relocations)
    original=diagnostics.output_fingerprint(b'object')
    assert original==diagnostics.output_fingerprint(b'another debug/source spelling')
    symbol.name='second'
    assert original!=diagnostics.output_fingerprint(b'object')
    symbol.name='first';data['data']=b'abce'
    assert original!=diagnostics.output_fingerprint(b'object')
    data['data']=b'abcd';bss['sh_size']=12
    assert original!=diagnostics.output_fingerprint(b'object')


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


def test_bounded_phase_capture_includes_only_requested_function_trace(tmp_path):
    from tests.test_rtl_value_trace import GREG, RTL
    (tmp_path/'func.c.rtl').write_text(RTL)
    (tmp_path/'func.c.flow').write_text(RTL)
    (tmp_path/'func.c.greg').write_text(GREG)
    result=diagnostics.phase_summary(tmp_path,'f')
    assert result['value_trace']['values'][0]['pseudo']==72
    assert not diagnostics.phase_summary(tmp_path,'missing')['value_trace']['values']


def test_priority_comparison_uses_unique_source_groups_not_renumbered_pseudos():
    from decompnet.app.gcc_allocation import priority_changes
    a = {'values':[{'pseudo':90,'source_lines':[12],'allocation_rank':0,'references':24,'live_insns':412,'hard_register':17}]}
    b = {'values':[{'pseudo':190,'source_lines':[12],'allocation_rank':2,'references':22,'live_insns':411,'hard_register':19}]}
    changes = priority_changes(a,b)
    assert changes[0]['changes']['references']=={'before':24,'after':22}
    assert 'bounded local variants' in changes[0]['next_action']
    assert not priority_changes(a, {'values':b['values']*2})
    assert not priority_changes(a, b | {'truncated':True})
    assert not diagnostics.compare({'output_unchanged':False}, {})['comparable']


def test_second_cse_only_change_survives_capture_and_comparison(tmp_path):
    (tmp_path/'func.c.cse2').write_text('(reg:SI 72)')
    before = diagnostics.phase_summary(tmp_path)
    (tmp_path/'func.c.cse2').write_text('(reg:SI 73) (reg:SI 74)')
    after = diagnostics.phase_summary(tmp_path)
    profile = {'output_unchanged':True, 'toolchain':'gcc-2.7.2-kmc', 'original_flags':['-O2']}
    result = diagnostics.compare(before | profile, after | profile)
    assert result['comparable']
    assert [p['phase'] for p in result['changed']] == ['cse2']
    assert result['changed'][0]['register_count_before'] == 1
    assert result['changed'][0]['register_count_after'] == 2
    assert result['lead'] == 'earlier optimization'


def test_all_supported_phases_compared_in_legacy_pipeline_order():
    def snapshot(digest):
        return {'output_unchanged':True, 'toolchain':'gcc-2.7.2-kmc', 'original_flags':['-O2'],
                'phases':{phase:{'captured_sha256':digest, 'register_count':1}
                          for phase in diagnostics.PHASES}}
    result = diagnostics.compare(snapshot('before'), snapshot('after'))
    phases = [p['phase'] for p in result['changed']]
    assert set(phases) == diagnostics.PHASES
    assert phases.index('loop') < phases.index('cse2') < phases.index('flow') < phases.index('greg')


def test_second_cse_keeps_truncation_and_profile_comparison_guards(tmp_path):
    (tmp_path/'func.c.cse2').write_text('(reg:SI 72)')
    before = diagnostics.phase_summary(tmp_path)
    (tmp_path/'func.c.cse2').write_text('(reg:SI 73)')
    after = diagnostics.phase_summary(tmp_path)
    profile = {'output_unchanged':True, 'toolchain':'gcc-2.7.2-kmc', 'original_flags':['-O2']}
    after['phases']['cse2']['truncated'] = True
    assert not diagnostics.compare(before | profile, after | profile)['changed']
    assert not diagnostics.compare(before | profile, after | (profile | {'original_flags':['-O1']}))['comparable']
    assert not diagnostics.compare(before | profile, after | (profile | {'output_unchanged':False}))['comparable']
