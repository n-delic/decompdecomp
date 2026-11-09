"""Fast ARM instruction shapes produce C candidates, and the pinned compiler/judge remain authoritative."""

import struct

import pytest

from decompnet import toolchains
from decompnet.arch import arm, arm_drafts
from decompnet.compiler import compile_source
from decompnet.project import compiler_config
from decompnet.rom.symbols import SymbolTable


@pytest.mark.parametrize('thumb,code', [
    (False, struct.pack('<II', 0xE3A0002A, 0xE12FFF1E)),  # constant return
    (False, struct.pack('<II', 0xE2800001, 0xE12FFF1E)),  # argument + 1
    (False, struct.pack('<II', 0xE5900008, 0xE12FFF1E)),  # field load
    (False, struct.pack('<II', 0xE5801008, 0xE12FFF1E)),  # field store
    (True, bytes.fromhex('2a 20 70 47')),
    (True, bytes.fromhex('01 30 70 47')),
    (True, bytes.fromhex('80 68 70 47')),
    (True, bytes.fromhex('81 60 70 47')),
])
def test_shapes_are_real_compiler_matches_in_both_instruction_modes(tmp_path, thumb, code):
    candidates = arm_drafts.exact(code, 0x1000, thumb, 'shape')
    assert candidates
    if toolchains.installed('android-clang-3.5') is None:
        pytest.skip('needs pinned Android Clang 3.5 to judge shape candidates')
    flags = [f for f in toolchains.get('android-clang-3.5').flags if f != '-mthumb'] + ['-mthumb' if thumb else '-marm']
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'android-clang-3.5', 'flags': flags}})
    result = compile_source(cfg, '', candidates[0])
    assert result.ok, result.log
    target = arm.ArmTarget('shape', 0x1000, code, thumb, thumb2=True)
    assert arm.link(result.obj, 'shape', target, SymbolTable(), lambda *_: None).exact


def test_shapes_do_not_override_a_shared_prototype_or_guess_conditional_code():
    code = bytes.fromhex('2a 20 70 47')
    assert not arm_drafts.exact(code, 0x1000, True, 'shape', 'char shape(void);')
    assert not arm_drafts.exact(struct.pack('<II', 0x03A0002A, 0xE12FFF1E), 0x1000, False, 'shape')
    assert not arm_drafts.exact(bytes.fromhex('00 f0 00 f8'), 0x1000, True, 'shape')
