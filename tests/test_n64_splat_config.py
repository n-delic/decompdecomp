"""Unsafe cartridge titles must not break splat's generated YAML."""

import hashlib
import struct

import pytest
import yaml

from decompnet.consoles.n64 import splat_config
from decompnet.rom import split


def _image(path):
    data = bytearray(0x1000)
    data[:4] = b'\x80\x37\x12\x40'
    data[0x20:0x34] = b'Example: A Game'.ljust(20, b' ')
    path.write_bytes(data)
    return bytes(data)


def test_splat_title_is_temporarily_sanitized_and_config_restored(tmp_path, monkeypatch):
    path = tmp_path / 'rom.z64'
    original = _image(path)

    def generate(args, workdir, log):
        assert path.read_bytes()[0x20:0x34] == b'Example A Game'.ljust(20, b' ')
        (workdir / 'game.yaml').write_text('name: Example A Game (Region)\nsha1: incorrect\n')
        return 'ok'

    monkeypatch.setattr(split, '_splat', generate)
    assert splat_config.create(['create_config', 'rom.z64'], tmp_path, lambda _: None) == 'ok'
    assert path.read_bytes() == original
    config = yaml.safe_load((tmp_path / 'game.yaml').read_text())
    assert config['name'] == 'Example: A Game (Region)'
    assert config['sha1'] == hashlib.sha1(original).hexdigest()


def test_import_copy_is_restored_even_if_splat_fails(tmp_path, monkeypatch):
    path = tmp_path / 'rom.z64'
    original = _image(path)

    def fail(*args):
        raise RuntimeError('failed')

    monkeypatch.setattr(split, '_splat', fail)
    with pytest.raises(RuntimeError):
        splat_config.create(['create_config', 'rom.z64'], tmp_path, lambda _: None)
    assert path.read_bytes() == original


def test_indirect_call_after_next_instruction_call_stays_outside_boot_stub():
    rom = bytearray(0x1100)
    vram = 0x80200400
    # Stack setup, call the immediately following function, delay slot,
    # then an indirect call in the callee.
    struct.pack_into('>5I', rom, 0x1000, 0x3c1d803f, 0x0c080103, 0, 0x0200f809, 0)
    assert splat_config._boot_call_size(rom, vram, 0x1000, 0x100) == 12
    from decompnet.consoles.n64.splat_boot_config import _parse
    result = _parse(rom, vram, size=0x100)
    assert result.entry_size == 12
    assert result.main_address.value == vram+12
    struct.pack_into('>I', rom, 0x1000, 0x10000001)  # an earlier branch makes the stub ambiguous
    assert splat_config._boot_call_size(rom, vram, 0x1000, 0x100) == 0x100
    struct.pack_into('>I', rom, 0x1000, 0x3c1d803f)
    struct.pack_into('>I', rom, 0x1004, 0x0c080120)  # the target is not the following function
    assert splat_config._boot_call_size(rom, vram, 0x1000, 0x100) == 0x100


def test_next_function_boot_uses_scoped_splat_wrapper(tmp_path, monkeypatch):
    path = tmp_path / 'rom.z64'
    data = bytearray(_image(path))
    data.extend(bytes(0x100))
    struct.pack_into('>I', data, 8, 0x80200400)
    struct.pack_into('>5I', data, 0x1000, 0x3c1d803f, 0x0c080103, 0, 0x0200f809, 0)
    path.write_bytes(data)
    called = []

    def generate(args, workdir, log, *, module):
        called.append(module)
        assert path.read_bytes()[0x1000:] == data[0x1000:]
        (workdir / 'game.yaml').write_text('name: Example A Game (Region)\nsha1: incorrect\n')
        return 'ok'

    monkeypatch.setattr(split, '_splat', generate)
    assert splat_config.create(['create_config', 'rom.z64'], tmp_path, lambda _: None) == 'ok'
    assert called == ['decompnet.consoles.n64.splat_boot_config']
    assert path.read_bytes() == data
    assert yaml.safe_load((tmp_path / 'game.yaml').read_text())['sha1'] == hashlib.sha1(data).hexdigest()
