"""Live link-register reads are fixed hardware operations, never submitted instruction streams."""
import struct

import pytest

from decompnet import toolchains
from decompnet.app import draft_repairs, live_return_address_repairs
from decompnet.arch import mips_debug_break, mips_live_return_address
from decompnet.compiler import SourceRejected, check_source, compile_source
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config

ASM='/* 0 80001000 3C01800B */ lui $at, 0x800b\n/* 4 80001004 AC3F2000 */ sw $ra, 0x2000($at)'
SOURCE='extern void *D_800B2000; void f(void) { void *unknown; D_800B2000=unknown; }'


def test_raw_store_evidence_ignores_stack_saves_gaps_calls_and_other_registers():
    assert mips_live_return_address.stores(ASM)=={0x800b2000}
    assert not mips_live_return_address.stores(ASM.replace('AC3F2000','AC222000'))
    assert not mips_live_return_address.stores(ASM.replace('AC3F2000','AFBF2000'))
    assert not mips_live_return_address.stores(ASM.replace('80001004','80001008'))
    assert not mips_live_return_address.stores('sw $ra, %lo(D_800B2000)($at)')
    assert not mips_live_return_address.stores(None)


def test_repair_requires_uninitialized_single_use_and_exact_raw_destination():
    choices=live_return_address_repairs.variants(SOURCE,'f','mips',ASM)
    assert len(choices)==1
    assert 'D_800B2000=__builtin_mips_live_return_address();' in choices[0][1]
    for source in (SOURCE.replace('void *unknown;','void *unknown=0;'),
                   SOURCE.replace('D_800B2000=unknown','escape(&unknown); D_800B2000=unknown'),
                   SOURCE.replace('D_800B2000','D_800B2004'),
                   SOURCE.replace('extern void *D_800B2000;',''),
                   '#define unknown other\n'+SOURCE):
        assert not live_return_address_repairs.variants(source,'f','mips',ASM)
    assert not live_return_address_repairs.variants(SOURCE,'f','ppc',ASM)
    labels={}
    choices=draft_repairs.variants(SOURCE,'f','mips',ASM,labels=labels,limit=None)
    assert any(labels[c]=='live_return_address' for c in choices)


def test_fixed_intrinsic_rejects_submitted_assembly_and_preserves_old_portable_headers():
    check_source('void *f(void) { return __builtin_mips_live_return_address(); }')
    with pytest.raises(SourceRejected):check_source('void *f(void) { register void *x __asm__("$31"); return x; }')
    tc=toolchains.get('gcc-2.7.2-kmc')
    assert toolchains.source_prelude(tc,'int f(void){return 1;}')==tc.prelude
    old=toolchains.source_prelude(tc,'void f(void){__builtin_mips_debug_break();}')
    assert old==tc.prelude+mips_debug_break._break_header()
    assert '__builtin_mips_live_return_address' not in old
    new=toolchains.source_prelude(tc,'void *f(void){return __builtin_mips_live_return_address();}')
    assert new==tc.prelude+mips_live_return_address.header()
    assert '__builtin_mips_debug_break' not in new


def config(tmp_path):
    if toolchains.installed('gcc-2.7.2-kmc') is None:pytest.skip('needs installed pinned GNU MIPS compiler')
    return compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc',
                'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}})


def words(result):
    assert result.ok,result.log
    code=extract_function(result.obj,'f').code
    return struct.unpack('>'+'I'*(len(code)//4),code)


def live_store(word):
    return word>>26==43 and (word>>16)&31==31 and (word>>21)&31!=29


def test_snapshots_are_current_before_and_after_callback_not_entry_caller(tmp_path):
    result=compile_source(config(tmp_path),'',
        'void f(void (*callback)(void), void **before, void **after) {'
        '*before=__builtin_mips_live_return_address(); callback();'
        '*after=__builtin_mips_live_return_address(); }')
    code=words(result)
    at_call=next(i for i,w in enumerate(code) if w>>26==0 and w&63==9)
    positions=[i for i,w in enumerate(code) if live_store(w)]
    assert len(positions)==2 and positions[0]<at_call<positions[1]


def test_saved_snapshot_survives_callback_and_new_read_is_still_live(tmp_path):
    result=compile_source(config(tmp_path),'',
        'void f(void (*callback)(void), void **before, void **after) {'
        'void *saved=__builtin_mips_live_return_address(); callback();'
        '*before=saved; *after=__builtin_mips_live_return_address(); }')
    code=words(result)
    at_call=next(i for i,w in enumerate(code) if w>>26==0 and w&63==9)
    stores=[w for w in code[at_call+1:] if w>>26==43 and (w>>21)&31!=29]
    assert len(stores)==2
    assert sum(live_store(w) for w in stores)==1


def test_no_operands_and_portable_direct_compiler_export(tmp_path):
    from decompnet.compiler import run_in
    cfg=config(tmp_path)
    assert not compile_source(cfg,'','void *f(void){ return __builtin_mips_live_return_address(31); }').ok
    source='void f(void **out){*out=__builtin_mips_live_return_address();}'
    app=compile_source(cfg,'',source)
    (tmp_path/'export.c').write_text(toolchains.source_prelude(toolchains.get('gcc-2.7.2-kmc'),source)+'\n#line 1 "func.c"\n'+source+'\n')
    argv=[x.replace('{src}','export.c').replace('{obj}','export.o').replace('{include}','.') for x in cfg.cmd]
    result=run_in(cfg,tmp_path,argv,inputs=['export.c'],outputs=['export.o'])
    assert result.returncode==0,result.stderr
    assert extract_function((tmp_path/'export.o').read_bytes(),'f').code==extract_function(app.obj,'f').code
