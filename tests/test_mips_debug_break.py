"""Fixed debugger traps do not permit submitted instruction streams or change ordinary C."""
import struct

import pytest

from decompnet import toolchains
from decompnet.compiler import SourceRejected, check_source, compile_source
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config


def test_debug_break_callers_are_c_but_submitted_assembly_is_rejected():
    check_source('void f(void) { __builtin_mips_debug_break(); }')
    for source in ('void f(void) { __asm__("break 9"); }',
                   '#define __builtin_mips_debug_break() __asm__("nop")\nvoid f(void){}'):
        with pytest.raises(SourceRejected, match='inline assembly'):check_source(source)


def test_fixed_trap_rejects_caller_instruction_parameters(tmp_path):
    if toolchains.installed('gcc-2.7.2-kmc') is None:pytest.skip('needs installed legacy MIPS GCC')
    cfg = compiler_config(tmp_path, {'compiler':{'toolchain':'gcc-2.7.2-kmc',
                            'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}})
    result = compile_source(cfg, '', 'void f(void) { __builtin_mips_debug_break(123); }')
    assert not result.ok


def test_debug_trap_and_resumption_compile_through_normal_judge_inputs(tmp_path):
    if toolchains.installed('gcc-2.7.2-kmc') is None:pytest.skip('needs installed legacy MIPS GCC')
    cfg = compiler_config(tmp_path, {'compiler':{'toolchain':'gcc-2.7.2-kmc',
                            'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}})
    result = compile_source(cfg, '', 'int f(int *p) { __builtin_mips_debug_break(); return *p; }')
    assert result.ok, result.log
    function = extract_function(result.obj, 'f')
    words = struct.unpack('>'+'I'*(len(function.code)//4), function.code)
    assert words.count(0x0009000D) == 1
    assert any(word >> 26 == 0x23 for word in words)  # execution can continue and load memory
    plain = 'int f(int x) { return x + 1; }'
    supported = compile_source(cfg, '', plain)
    cfg.intrinsics = ''
    original = compile_source(cfg, '', plain)
    assert original.ok and supported.ok
    assert extract_function(original.obj, 'f').code == extract_function(supported.obj, 'f').code
