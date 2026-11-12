"""Fixed control-register I/O keeps operand allocation with the period compiler."""
import struct

import pytest

from decompnet import toolchains
from decompnet.arch import mips_debug_break, mips_fcsr
from decompnet.compiler import SourceRejected, check_source, compile_source, run_in
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config
from decomprepair import mips_live_return_address

SOURCE='unsigned f(unsigned value){return __builtin_mips_exchange_fcsr(value);}'


def config(tmp_path):
    if toolchains.installed('gcc-2.7.2-kmc') is None:
        pytest.skip('needs installed pinned GNU MIPS compiler')
    return compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc',
        'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}})


def words(result):
    assert result.ok,result.log
    code=extract_function(result.obj,'f').code
    return struct.unpack('>'+'I'*(len(code)//4),code)


def test_only_the_used_fixed_template_is_exported():
    tc=toolchains.get('gcc-2.7.2-kmc')
    assert toolchains.source_prelude(tc,SOURCE)==tc.prelude+mips_fcsr.header()
    assert toolchains.source_prelude(tc,'int f(void){return 0;}')==tc.prelude
    assert toolchains.source_prelude(tc,'void f(void){__builtin_mips_debug_break();}')==tc.prelude+mips_debug_break._break_header()
    assert toolchains.source_prelude(tc,'void *f(void){return __builtin_mips_live_return_address();}')==tc.prelude+mips_live_return_address.header()
    assert '"=&r"' in mips_fcsr.header() and '"r"' in mips_fcsr.header()
    assert '"memory"' in mips_fcsr.header()
    assert 'nop' not in mips_fcsr.header() and '$2' not in mips_fcsr.header()


def test_source_cannot_supply_registers_opcodes_or_replace_the_template():
    check_source(SOURCE)
    for source in ('unsigned f(unsigned v){__asm__("ctc1 $4,$31");return v;}',
                   '#define __builtin_mips_exchange_fcsr(x) __asm__(x)\n'+SOURCE):
        with pytest.raises(SourceRejected):check_source(source)


def test_exchange_returns_old_word_and_preserves_distinct_new_word_register(tmp_path):
    code=words(compile_source(config(tmp_path),'',SOURCE))
    assert code==(0x4442f800,0x44c4f800,0x03e00008,0)
    assert (code[0]>>16)&31 != (code[1]>>16)&31


def test_operand_is_evaluated_once_and_unused_result_still_writes_fcsr(tmp_path):
    code=words(compile_source(config(tmp_path),'',
        'unsigned f(volatile unsigned *p){return __builtin_mips_exchange_fcsr((*p)++); }'))
    assert sum(w>>26==35 for w in code)==1
    assert sum(w>>26==43 for w in code)==1
    assert sum(w&0xffe0ffff==0x4440f800 for w in code)==1
    assert sum(w&0xffe0ffff==0x44c0f800 for w in code)==1
    code=words(compile_source(config(tmp_path),'',
        'void f(unsigned x){__builtin_mips_exchange_fcsr(x);}'))
    assert any(w&0xffe0ffff==0x44c0f800 for w in code)


def test_arity_and_nonscalar_inputs_are_rejected_by_the_period_compiler(tmp_path):
    cfg=config(tmp_path)
    for source in ('unsigned f(void){return __builtin_mips_exchange_fcsr();}',
                   'unsigned f(void){return __builtin_mips_exchange_fcsr(1,31);}',
                   'struct X {int a;}; unsigned f(struct X x){return __builtin_mips_exchange_fcsr(x);}'):
        assert not compile_source(cfg,'',source).ok


def test_direct_export_and_app_compile_have_identical_hardware_operations(tmp_path):
    cfg=config(tmp_path)
    app=compile_source(cfg,'',SOURCE)
    (tmp_path/'export.c').write_text(toolchains.source_prelude(toolchains.get('gcc-2.7.2-kmc'),SOURCE)+'\n#line 1 "func.c"\n'+SOURCE+'\n')
    argv=[x.replace('{src}','export.c').replace('{obj}','export.o').replace('{include}','.') for x in cfg.cmd]
    result=run_in(cfg,tmp_path,argv,inputs=['export.c'],outputs=['export.o'])
    assert result.returncode==0,result.stderr
    assert extract_function((tmp_path/'export.o').read_bytes(),'f').code==extract_function(app.obj,'f').code


ASM=('glabel sample\n/* 0 80001000 4442F800 */ cfc1 $v0,$31\n'
     '/* 4 80001004 44C4F800 */ ctc1 $a0,$31\n'
     '/* 8 80001008 03E00008 */ jr $ra\n/* C 8000100C 00000000 */ nop\nendlabel sample\n')


def test_machine_seed_requires_complete_architectural_leaf_and_registered_compiler():
    from decompnet import seeds
    seed=mips_fcsr.candidate(ASM,'f','gcc-2.7.2-kmc')
    assert seed=='unsigned int f(unsigned int value) { return __builtin_mips_exchange_fcsr(value); }\n'
    assert seed and '__asm__' not in seed
    assert seeds.generate_from_asm(ASM,'f','gcc-2.7.2-kmc')==seed
    for text in (ASM.replace('4442F800','4443F800'),ASM.replace('44C4F800','44C5F800'),
                 ASM.replace('F800','F000'),ASM.replace('00000000','00000001'),
                 ASM.replace('80001004','80001008'),ASM.replace('03E00008','03200008'),
                 ASM+'nop\n',ASM+'/* extra */ nop\n'):
        assert mips_fcsr.candidate(text,'f','gcc-2.7.2-kmc') is None
    for compiler in ('ido-5.3','unknown','mwcc-2.6'):
        assert mips_fcsr.candidate(ASM,'f',compiler) is None
    assert mips_fcsr.candidate(ASM,'f()','gcc-2.7.2-kmc') is None


def test_automatic_machine_seed_compiles_through_the_normal_pipeline(tmp_path):
    from decompnet import seeds
    source=seeds.generate_from_asm(ASM,'f','gcc-2.7.2-kmc')
    assert words(compile_source(config(tmp_path),'',source))==(0x4442f800,0x44c4f800,0x03e00008,0)
