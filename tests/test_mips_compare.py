"""CP0 Compare access is fixed hardware I/O, not source-supplied assembly."""
import pytest

from decompnet import seeds, toolchains
from decompnet.arch import mips_compare
from decompnet.compiler import SourceRejected, check_source, compile_source
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config

SOURCE='void f(unsigned int value) { __builtin_mips_write_compare(value); }'
ASM=('glabel sample\n/* 0 80001000 40845800 */ mtc0 $a0,$11\n'
     '/* 4 80001004 03E00008 */ jr $ra\n'
     '/* 8 80001008 00000000 */ nop\nendlabel sample\n')


def test_seed_is_a_complete_compare_write_leaf_for_registered_backend():
    assert mips_compare.candidate(ASM,'f','gcc-2.7.2-kmc')==SOURCE+'\n'
    assert seeds.generate_from_asm(ASM,'f','gcc-2.7.2-kmc')==SOURCE+'\n'
    for bad in (ASM.replace('40845800','40845000'),ASM.replace('40845800','40855800'),
                ASM.replace('40845800','40045800'),ASM.replace('03E00008','03200008'),
                ASM.replace('00000000','00000001'),ASM.replace('80001004','80001008'),
                ASM+'nop\n',ASM+'/* extra */ nop\n',ASM+'#define X 1\n',ASM.replace('40845800','40845801')):
        assert mips_compare.candidate(bad,'f','gcc-2.7.2-kmc') is None
    for tc in ('ido-5.3','unknown','mwcc-2.6'):
        assert mips_compare.candidate(ASM,'f',tc) is None
    assert mips_compare.candidate(ASM,'f()','gcc-2.7.2-kmc') is None


def test_export_only_adds_used_fixed_intrinsic():
    tc=toolchains.get('gcc-2.7.2-kmc')
    assert toolchains.source_prelude(tc,SOURCE)==tc.prelude+mips_compare.header()
    assert toolchains.source_prelude(tc,'int f(void){return 0;}')==tc.prelude
    assert '"r"' in mips_compare.header() and '"memory"' in mips_compare.header()
    assert '$2' not in mips_compare.header() and 'nop' not in mips_compare.header()
    check_source(SOURCE)
    for bad in ('unsigned f(void){__asm__("mfc0 $2,$12");return 0;}',
                '#define __builtin_mips_write_compare() __asm__("nop")\n'+SOURCE):
        with pytest.raises(SourceRejected):check_source(bad)


def test_real_compiler_preserves_writes_and_matches_ordinary_and_diagnostic(tmp_path):
    if toolchains.installed('gcc-2.7.2-kmc') is None:pytest.skip('needs pinned compiler')
    for flags in ([],['-da']):
        cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc',
            'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']+flags}})
        result=compile_source(cfg,'',SOURCE)
        assert result.ok,result.log
        assert extract_function(result.obj,'f').code.hex()=='4084580003e0000800000000'
        result=compile_source(cfg,'','void f(unsigned a,unsigned b){__builtin_mips_write_compare(a); __builtin_mips_write_compare(b);}')
        assert result.ok,result.log
        code=extract_function(result.obj,'f').code
        assert sum(int.from_bytes(code[i:i+4],'big')&0xffe0ffff==0x40805800 for i in range(0,len(code),4))==2
        assert not compile_source(cfg,'','void f(void){__builtin_mips_write_compare();}').ok
        assert not compile_source(cfg,'','void f(void){__builtin_mips_write_compare(1,2);}').ok
