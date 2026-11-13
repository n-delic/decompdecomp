"""CP0 Status return-spacing access is fixed hardware I/O, not source-supplied assembly."""
import pytest

from decompnet import seeds, toolchains
from decompnet.arch import mips_status_return_spacing
from decompnet.compiler import SourceRejected, check_source, compile_source
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config

SOURCE='void f(int value) { __builtin_mips_write_status_return_spacing(value); }'
ASM=('glabel sample\n/* 0 80001000 40846000 */ mtc0 $a0,$12\n'
     '/* 4 80001004 00000000 */ nop\n'
     '/* 8 80001008 03E00008 */ jr $ra\n'
     '/* C 8000100C 00000000 */ nop\nendlabel sample\n')


def test_seed_is_a_complete_status_return_spacing_write_leaf_for_registered_backend():
    assert seeds.intrinsic_leaf(ASM,'f','gcc-2.7.2-kmc')==SOURCE+'\n'
    assert seeds.generate_from_asm(ASM,'f','gcc-2.7.2-kmc')==SOURCE+'\n'
    for bad in (ASM.replace('40846000','40846800'),ASM.replace('40846000','40856000'),
                ASM.replace('40846000','40046000'),ASM.replace('03E00008','03200008'),
                ASM.replace('00000000','00000001'),ASM.replace('80001004','80001008'),
                ASM+'nop\n',ASM+'/* extra */ nop\n',ASM+'#define X 1\n',ASM.replace('40846000','40846001')):
        assert mips_status_return_spacing.candidate(bad,'f',True) is None
    for bad in (ASM.replace('/* 4 80001004 00000000 */ nop\n',''),
                ASM.replace('00000000 */ nop','00000001 */ nop'),
                ASM.replace('endlabel sample','/* 10 80001010 00000000 */ nop\nendlabel sample')):
        assert mips_status_return_spacing.candidate(bad,'f',True) is None
    for tc in ('ido-5.3','unknown','mwcc-2.6','gcc-2.8.1-pm'):
        assert seeds.intrinsic_leaf(ASM,'f',tc) is None
    assert seeds.intrinsic_leaf(ASM,'f()','gcc-2.7.2-kmc') is None


def test_export_only_adds_used_fixed_intrinsic():
    tc=toolchains.get('gcc-2.7.2-kmc')
    assert toolchains.source_prelude(tc,SOURCE)==tc.prelude+mips_status_return_spacing.header()
    assert toolchains.source_prelude(tc,'int f(void){return 0;}')==tc.prelude
    assert '"r"' in mips_status_return_spacing.header() and '"memory"' in mips_status_return_spacing.header()
    assert 'jr' not in mips_status_return_spacing.header()
    assert mips_status_return_spacing.header().count('nop')==1
    assert '.set noreorder' in mips_status_return_spacing.header()
    assert '.set reorder' in mips_status_return_spacing.header()
    assert '$2' not in mips_status_return_spacing.header() and 'nop' in mips_status_return_spacing.header()
    check_source(SOURCE)
    for bad in ('unsigned f(void){__asm__("mfc0 $2,$12");return 0;}',
                '#define __builtin_mips_write_status_return_spacing() __asm__("nop")\n'+SOURCE):
        with pytest.raises(SourceRejected):check_source(bad)


def test_real_compiler_preserves_writes_and_matches_ordinary_and_diagnostic(tmp_path):
    if toolchains.installed('gcc-2.7.2-kmc') is None:pytest.skip('needs pinned compiler')
    for flags in ([],['-da']):
        cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc',
            'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']+flags}})
        result=compile_source(cfg,'',SOURCE)
        assert result.ok,result.log
        assert extract_function(result.obj,'f').code.hex()=='408460000000000003e0000800000000'
        result=compile_source(cfg,'','void f(unsigned a,unsigned b){__builtin_mips_write_status_return_spacing(a); __builtin_mips_write_status_return_spacing(b);}')
        assert result.ok,result.log
        code=extract_function(result.obj,'f').code
        assert sum(int.from_bytes(code[i:i+4],'big')&0xffe0ffff==0x40806000 for i in range(0,len(code),4))==2
        assert not compile_source(cfg,'','void f(void){__builtin_mips_write_status_return_spacing();}').ok
        assert not compile_source(cfg,'','void f(void){__builtin_mips_write_status_return_spacing(1,2);}').ok


def test_real_compiler_restores_neighboring_branch_delay_slots(tmp_path):
    if toolchains.installed('gcc-2.7.2-kmc') is None:pytest.skip('needs pinned compiler')
    body='unsigned before(unsigned x){return x?x+2:7;}\n'+SOURCE+'\nunsigned after(unsigned x){return x?x+2:7;}'
    for flags in ([],['-da']):
        cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc',
            'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']+flags}})
        result=compile_source(cfg,'',body)
        assert result.ok,result.log
        # Identically shaped neighbors, including their branch/return delay slots,
        # must not inherit noreorder from the fixed operation between them.
        assert extract_function(result.obj,'before').code==extract_function(result.obj,'after').code
        assert extract_function(result.obj,'f').code.hex()=='408460000000000003e0000800000000'
