"""Recursive ROM lookup only offers hash-verified files and never moves originals or builds projects."""

import hashlib
from types import SimpleNamespace

import pytest

from decompnet.app import romlibrary


def project(root, platform='n64'):
    return SimpleNamespace(root=root, detect_platform=lambda: platform)


def needs(data):
    return {'path': 'baserom.z64', 'sha1': hashlib.sha1(data).hexdigest()}


@pytest.mark.parametrize('order', ['z64', 'v64', 'n64'])
def test_nested_n64_files_match_in_any_byte_order_without_filename_hint(tmp_path, order):
    rom = b'\x80\x37\x12\x40' + bytes(range(256)) * 1024
    original = rom
    if order == 'v64':
        rom = b''.join(rom[i:i+2][::-1] for i in range(0, len(rom), 2))
    elif order == 'n64':
        rom = b''.join(rom[i:i+4][::-1] for i in range(0, len(rom), 4))
    library = tmp_path / 'library/nested/more'
    library.mkdir(parents=True)
    file = library / 'some game.unknown'
    file.write_bytes(rom)
    root = tmp_path / 'project'
    root.mkdir()
    found = romlibrary.find(project(root), needs(original), str(tmp_path / 'library'), log=lambda *_: None)
    assert found['path'] == str(file) and not found['in_project']
    assert file.read_bytes() == rom and not list(root.iterdir())


def test_existing_rom_wins_even_without_library_setting_and_wrong_rom_does_not(tmp_path):
    file = tmp_path / 'baserom.z64'
    file.write_bytes(b'valid')
    assert romlibrary.find(project(tmp_path), needs(b'valid'), '')['in_project']
    file.write_bytes(b'wrong')
    assert romlibrary.find(project(tmp_path), needs(b'valid'), '')['path'] is None


def test_cache_is_invalidated_by_file_changes_and_alternate_version_matches(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    lib = tmp_path / 'roms'
    lib.mkdir()
    file = lib / 'game.bin'
    file.write_bytes(b'first')
    cfg = needs(b'other')
    assert romlibrary.find(project(root, 'ps'), cfg, str(lib))['path'] is None
    file.write_bytes(b'other')
    cfg['rom_versions'] = [{'version': 'other', 'sha1': hashlib.sha1(b'other').hexdigest()}]
    cfg['sha1'] = '0' * 40
    assert romlibrary.find(project(root, 'ps'), cfg, str(lib))['path'] == str(file)


def test_missing_hash_or_folder_and_symlink_loop(tmp_path):
    p = project(tmp_path)
    assert romlibrary.find(p, {}, str(tmp_path))['path'] is None
    assert 'unavailable' in romlibrary.find(p, needs(b'x'), str(tmp_path / 'absent'))['reason']
    loop = tmp_path / 'loop'
    try:
        loop.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip('symlinks unavailable')
    assert romlibrary.find(p, needs(b'x'), str(tmp_path))['path'] is None


def test_windows_rehashes_when_same_size_overwrite_keeps_stat_timestamps(tmp_path, monkeypatch):
    file = tmp_path / 'game.bin'
    file.write_bytes(b'first')
    stat_before = file.stat()
    original_stat = type(file).stat
    def unchanged_stat(path, *args, **kwargs):
        return stat_before if path == file else original_stat(path, *args, **kwargs)
    monkeypatch.setattr(type(file), 'stat', unchanged_stat)
    monkeypatch.setattr(romlibrary, 'CACHE_HASHES', False)
    assert romlibrary.fingerprint(file, 'ps') == hashlib.sha1(b'first').hexdigest()
    file.write_bytes(b'other')
    assert romlibrary.fingerprint(file, 'ps') == hashlib.sha1(b'other').hexdigest()
