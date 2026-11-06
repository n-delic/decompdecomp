"""CP0 Status access is fixed hardware I/O, not source-supplied assembly."""
import pytest

from decompnet import seeds, toolchains
from decompnet.arch import mips_status
from decompnet.compiler import SourceRejected, check_source, compile_source
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config

SOURCE='unsigned int f(void) { return __builtin_mips_read_status(); }'
ASM=('glabel sample\n/* 0 80001000 40026000 */ mfc0 $v0,$12\n'
     '/* 4 80001004 03E00008 */ jr $ra\n'
     '/* 8 80001008 00000000 */ nop\nendlabel sample\n')


def test_seed_is_a_complete_status_read_leaf_for_registered_backend():
    assert mips_status.candidate(ASM,'f','gcc-2.7.2-kmc')==SOURCE+'\n'
    assert seeds.generate_from_asm(ASM,'f','gcc-2.7.2-kmc')==SOURCE+'\n'
    for bad in (ASM.replace('40026000','40024800'),ASM.replace('40026000','40036000'),
                ASM.replace('40026000','40826000'),ASM.replace('03E00008','03200008'),
                ASM.replace('00000000','00000001'),ASM.replace('80001004','80001008'),
                ASM+'nop\n',ASM+'/* extra */ nop\n',ASM+'#define X 1\n',ASM.replace('40026000','40026001')):
        assert mips_status.candidate(bad,'f','gcc-2.7.2-kmc') is None
    for tc in ('ido-5.3','unknown','mwcc-2.6'):
        assert mips_status.candidate(ASM,'f',tc) is None
    assert mips_status.candidate(ASM,'f()','gcc-2.7.2-kmc') is None


def test_export_only_adds_used_fixed_intrinsic():
    tc=toolchains.get('gcc-2.7.2-kmc')
    assert toolchains.source_prelude(tc,SOURCE)==tc.prelude+mips_status.header()
    assert toolchains.source_prelude(tc,'int f(void){return 0;}')==tc.prelude
    assert '"=r"' in mips_status.header() and '"memory"' in mips_status.header()
    assert '$2' not in mips_status.header() and 'nop' not in mips_status.header()
    check_source(SOURCE)
    for bad in ('unsigned f(void){__asm__("mfc0 $2,$12");return 0;}',
                '#define __builtin_mips_read_status() __asm__("nop")\n'+SOURCE):
        with pytest.raises(SourceRejected):check_source(bad)


def test_real_compiler_preserves_reads_and_matches_ordinary_and_diagnostic(tmp_path):
    if toolchains.installed('gcc-2.7.2-kmc') is None:pytest.skip('needs pinned compiler')
    for flags in ([],['-da']):
        cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc',
            'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']+flags}})
        result=compile_source(cfg,'',SOURCE)
        assert result.ok,result.log
        assert extract_function(result.obj,'f').code.hex()=='4002600003e0000800000000'
        result=compile_source(cfg,'','unsigned f(void){unsigned a=__builtin_mips_read_status(); return __builtin_mips_read_status()-a;}')
        assert result.ok,result.log
        code=extract_function(result.obj,'f').code
        assert sum(int.from_bytes(code[i:i+4],'big')&0xffe0ffff==0x40006000 for i in range(0,len(code),4))==2
        result=compile_source(cfg,'','void f(void){__builtin_mips_read_status();}')
        assert result.ok,result.log
        code=extract_function(result.obj,'f').code
        assert any(int.from_bytes(code[i:i+4],'big')&0xffe0ffff==0x40006000 for i in range(0,len(code),4))
        assert not compile_source(cfg,'','unsigned f(void){return __builtin_mips_read_status(9);}').ok
