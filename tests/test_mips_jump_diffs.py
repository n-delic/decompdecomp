"""Internal jump destinations must remain visible after relocation masking."""

import pytest

from decompnet import scoring
from decompnet.elfobj import FunctionObj, Reloc


@pytest.mark.parametrize('big', [True, False])
@pytest.mark.parametrize('opcode,mnemonic', [(0x08000000, 'j'), (0x0c000000, 'jal')])
def test_jump_relocation_word_addends_render_as_byte_offsets(big, opcode, mnemonic):
    code = opcode.to_bytes(4, 'big' if big else 'little')
    target = FunctionObj('f', 'mips', big, code, [Reloc(0, 4, 'f', 15)])
    candidate = FunctionObj('f', 'mips', big, code, [Reloc(0, 4, 'f', 16)])
    d = scoring.diff(target, candidate)
    assert not d.bytes_match
    assert d.rows == [('i', f'{mnemonic} f+0x3c', f'{mnemonic} f+0x40')]
    assert scoring.first_mismatch(d.rows)[2] is not None
    assert scoring.diff(target, target).bytes_match
    external = FunctionObj('f', 'mips', big, code, [Reloc(0, 4, 'callee', 0)])
    assert scoring.disassemble(external)[0].text == f'{mnemonic} callee'
