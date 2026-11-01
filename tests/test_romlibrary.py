"""Recursive ROM lookup only offers hash-verified files and never moves originals or builds projects."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import romlibrary
from decompnet.forks.project import ForkError, ForkProject


def project(root, platform='n64'):
    return SimpleNamespace(root=root, platform=platform)


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


def test_setup_accepts_verified_file_already_at_destination(tmp_path):
    game = tmp_path / 'baserom.z64'
    game.write_bytes(b'valid')
    (tmp_path / '.decomp.wtf.json').write_text(json.dumps({'game': needs(b'valid')}))
    fork = ForkProject(tmp_path)
    assert fork.place_game(game, game.name) == game
    assert game.read_bytes() == b'valid'
    game.write_bytes(b'wrong')
    with pytest.raises(ForkError, match='does not match'):
        fork.place_game(game, game.name)


def test_byte_swapped_file_in_project_is_not_offered_for_self_copy(tmp_path):
    original = b'\x80\x37\x12\x40' + bytes(range(12))
    swapped = b''.join(original[i:i+2][::-1] for i in range(0, len(original), 2))
    (tmp_path / 'baserom.z64').write_bytes(swapped)
    assert romlibrary.find(project(tmp_path), needs(original), '')['path'] is None


def test_n64_target_extension_does_not_control_console_identity(tmp_path):
    original = b'\x80\x37\x12\x40' + bytes(range(256)) * 1024
    swapped = b''.join(original[i:i+2][::-1] for i in range(0, len(original), 2))
    library = tmp_path / 'library'
    library.mkdir()
    (library / 'game.v64').write_bytes(swapped)
    root = tmp_path / 'project'
    root.mkdir()
    cfg = {'path': 'baserom.n64', 'sha1': hashlib.sha1(original).hexdigest()}
    assert romlibrary.find(project(root), cfg, str(library))['path'] == str(library / 'game.v64')
    assert romlibrary.find(project(root, ''), cfg, str(library))['path'] == str(library / 'game.v64')


def test_unknown_platform_still_rejects_too_small_n64_file(tmp_path):
    library = tmp_path / 'library'
    library.mkdir()
    (library / 'tiny.rom').write_bytes(b'\x80\x37\x12\x40' + bytes(12))
    root = tmp_path / 'project'
    root.mkdir()
    found = romlibrary.find(project(root, ''), needs((library / 'tiny.rom').read_bytes()), str(library))
    assert found['path'] is None and found['scanned'] == 0


def test_search_skips_archives_and_caps_file_count(tmp_path, monkeypatch):
    library = tmp_path / 'library'
    library.mkdir()
    (library / 'archive.zip').write_bytes(b'game')
    (library / 'a.bin').write_bytes(b'wrong')
    (library / 'b.bin').write_bytes(b'game')
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.setattr(romlibrary, 'MAX_FILES', 1)
    found = romlibrary.find(project(root, 'ps'), needs(b'game'), str(library))
    assert found['path'] is None
    assert 'stopped' in found['reason']


def test_unreadable_directory_is_reported(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    library = tmp_path / 'library'
    library.mkdir()

    def walk(path, *, followlinks, onerror):
        onerror(PermissionError('cannot read'))
        return iter(())

    monkeypatch.setattr(romlibrary.os, 'walk', walk)
    found = romlibrary.find(project(root), needs(b'game'), str(library))
    assert found['skipped'] == 1


def test_directory_junction_is_not_traversed(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    library = tmp_path / 'library'
    library.mkdir()
    junction = library / 'junction'
    junction.mkdir()
    (junction / 'game.bin').write_bytes(b'game')
    monkeypatch.setattr(romlibrary.os.path, 'isjunction', lambda path: Path(path).name == 'junction')
    assert romlibrary.find(project(root, 'ps'), needs(b'game'), str(library))['path'] is None


def test_directory_symlink_is_not_traversed(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    library = tmp_path / 'library'
    library.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'game.bin').write_bytes(b'game')
    try:
        (library / 'link').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlinks unavailable')
    assert romlibrary.find(project(root, 'ps'), needs(b'game'), str(library))['path'] is None


def test_cache_is_invalidated_by_file_changes(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    lib = tmp_path / 'roms'
    lib.mkdir()
    file = lib / 'game.bin'
    file.write_bytes(b'first')
    cfg = needs(b'other')
    assert romlibrary.find(project(root, 'ps'), cfg, str(lib))['path'] is None
    file.write_bytes(b'other')
    cfg['sha1'] = hashlib.sha1(b'other').hexdigest()
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


def test_library_skips_symlinked_game_files(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    game = outside / 'game.bin'
    game.write_bytes(b'game')
    library = tmp_path / 'library'
    library.mkdir()
    try:
        (library / 'game.bin').symlink_to(game)
    except OSError:
        pytest.skip('symlinks unavailable')
    project_root = tmp_path / 'project'
    project_root.mkdir()
    assert romlibrary.find(project(project_root), needs(b'game'), str(library))['path'] is None


def test_windows_rehashes_after_same_size_overwrite_with_unchanged_timestamps(tmp_path, monkeypatch):
    file = tmp_path / 'game.bin'
    file.write_bytes(b'first')
    stat_before = file.stat()
    original_stat = type(file).stat

    def unchanged_stat(path, *args, **kwargs):
        return stat_before if path == file else original_stat(path, *args, **kwargs)

    monkeypatch.setattr(type(file), 'stat', unchanged_stat)
    monkeypatch.setattr(romlibrary, 'CACHE_HASHES', False)
    assert romlibrary.fingerprint(file, 'raw') == hashlib.sha1(b'first').hexdigest()
    file.write_bytes(b'other')
    assert romlibrary.fingerprint(file, 'raw') == hashlib.sha1(b'other').hexdigest()
