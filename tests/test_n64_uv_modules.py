"""Validate section-relative relocations in native cartridge modules."""

import struct

import pytest

from decompnet import toolchains
from decompnet.compiler import compile_source
from decompnet.consoles.n64 import uso, uso_match, uv_modules
from decompnet.project import compiler_config


def _chunk(tag, data):
    return tag + struct.pack('>I', len(data)) + data


def _module(*, kind=2, offset=4, section=0, code=None):
    code = code or struct.pack('>8I', 0x27bdfff8, 0x0c000004, 0, 0, 0x03e00008, 0, 0, 0)
    common = struct.pack('>7I4s2I', 16, 0, len(code), 0, 0, 16, 1, b'CODE', 0, 0)
    relocation = struct.pack('>I', (section << 26) | (kind << 22) | (offset // 2))
    chunks = [(b'PAD ', bytes(4)), (b'COMM', common), (b'CODE', code),
              (b'MDBG', bytes(32)), (b'RELA', relocation + bytes(4))]
    return _chunk(b'FORM', b'MODU' + b''.join(_chunk(tag, data) for tag, data in chunks))


def test_reads_cartridge_module_without_altering_its_code():
    image = bytes(8) + _module()
    module, = uv_modules.read(image)
    assert module.offset == 8
    assert module.text_size == 32 and module.entry == 0
    assert struct.unpack_from('>I', image, module.code_offset)[0] == 0x27bdfff8
    assert module.relocations == (uv_modules.Relocation(offset=4, section=0, target=0, kind=2),)


@pytest.mark.parametrize('kwargs', [dict(kind=7), dict(offset=32), dict(offset=2), dict(section=3)])
def test_rejects_unrecognised_or_out_of_bounds_relocations(kwargs):
    assert uv_modules.read(_module(**kwargs)) == []


def test_rejects_truncated_module():
    assert uv_modules.read(_module()[:-1]) == []


def test_import_uses_raw_bytes_and_preserves_native_bindings(tmp_path):
    from decompnet.rom.split import SplitResult

    code = bytes.fromhex('27bdffe8 afbf0014 0c000000 00000000 8fbf0014 27bd0018 03e00008 00000000')
    original = _module(code=code, offset=8)
    image = tmp_path / 'rom.z64'
    image.write_bytes(original)
    result = SplitResult([], [], [], {}, tmp_path/'splat.yaml', 'IDO', '', image=image.name)
    uso.add_to_split(result, tmp_path, original, lambda *_: None)
    function, = result.functions
    metadata = function.uso
    assert function.vram == 0
    assert metadata['relocs'] == [[8, 4, 1]]
    assert metadata['size'] == len(code)
    assert image.read_bytes() == original
    analysis = (tmp_path/result.image).read_bytes()
    assert analysis[function.rom:function.rom+function.size] == code
    assert analysis.startswith(original)

    if toolchains.installed('ido-5.3') is None:
        pytest.skip('needs IDO 5.3 to test rebuilding a matched overlay function')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-5.3', 'flags': toolchains.get('ido-5.3').flags}})
    binding = uso_match.alias(metadata['section'], 1)
    source = f'extern void {binding}(void); void test_entry(void) {{ {binding}(); }}'
    built = compile_source(cfg, '', source)
    assert built.ok, built.log
    assert uso.rebuild(original, [('test_entry', metadata, built.obj)]) == original
    wrong = compile_source(cfg, '', source.replace(binding, uso_match.alias(metadata['section'], 2)))
    assert wrong.ok, wrong.log
    with pytest.raises(ValueError, match='no longer matches'):
        uso.rebuild(original, [('test_entry', metadata, wrong.obj)])
