"""Unsafe cartridge titles must not break splat's generated YAML."""

import hashlib

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
