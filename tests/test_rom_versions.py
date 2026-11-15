"""A library ROM's normalized identity must select the matching public Splat build."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import romlibrary
from decompnet.app.forkengine import ForkEngine
from decompnet.forks.project import ForkProject
from decompnet.network import NetworkError


def rom(letter):
    return b'\x80\x37\x12\x40' + letter * (256 * 1024)


def project(root: Path):
    (root / 'Makefile').write_text('BASENAME := game\nVERSION ?= us\n'
                                   'extract:\n\tpython -m splat split $(BASENAME).$(VERSION).yaml\n')
    games = {'us': rom(b'U'), 'pal': rom(b'P')}
    for version, data in games.items():
        (root / f'game.{version}.yaml').write_text(
            f'sha1: {hashlib.sha1(data).hexdigest()}\noptions:\n'
            f'  platform: n64\n  base_path: .\n  target_path: baserom.{version}.z64\nsegments: []\n')
    return ForkProject(root), games


def test_library_selects_byte_swapped_version_for_placement_and_build(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    fp, games = project(root)
    need = fp.needs()
    assert need['path'] == 'baserom.us.z64'
    assert {v['version'] for v in need['rom_versions']} == {'us', 'pal'}

    library = tmp_path / 'library'
    library.mkdir()
    swapped = b''.join(games['pal'][i:i + 2][::-1] for i in range(0, len(games['pal']), 2))
    copy = library / 'my-copy.v64'
    copy.write_bytes(swapped)
    found = romlibrary.find(SimpleNamespace(root=root, detect_platform=lambda: 'n64'), need,
                            str(library), log=lambda *_: None)
    assert found['path'] == str(copy) and found['version'] == 'pal' and not found['in_project']

    engine = object.__new__(ForkEngine)
    cfg = {}
    monkeypatch.setattr(ForkEngine, '_save_cfg', lambda *_: None)
    engine._match_version(SimpleNamespace(), fp, cfg, copy, log=lambda *_: None)
    assert cfg['version'] == 'pal'
    dest = fp.place_game(copy, copy.name, cfg['version'])
    assert dest == root / 'baserom.pal.z64'
    assert dest.read_bytes() == games['pal']
    assert fp.setup_commands(cfg['version'])[0] == ['make', 'VERSION=pal', 'extract']
    assert fp.setup_commands(cfg['version'])[1][1] == 'VERSION=pal'
    assert copy.read_bytes() == swapped

    (root / 'baserom.us.z64').write_bytes(games['pal'])
    misplaced = romlibrary.find(SimpleNamespace(root=root, detect_platform=lambda: 'n64'), need,
                                '', log=lambda *_: None)
    assert misplaced['version'] == 'pal' and not misplaced['in_project']

    swapped_at_destination = root / 'baserom.us.z64'
    swapped_at_destination.write_bytes(
        b''.join(games['us'][i:i + 2][::-1] for i in range(0, len(games['us']), 2)))
    found_in_place = romlibrary.find(SimpleNamespace(root=root, detect_platform=lambda: 'n64'), need,
                                     '', log=lambda *_: None)
    assert found_in_place['path'] == str(swapped_at_destination) and not found_in_place['in_project']
    assert fp.place_game(swapped_at_destination, swapped_at_destination.name, 'us') == swapped_at_destination
    assert swapped_at_destination.read_bytes() == games['us']

    copy.write_bytes(rom(b'X'))
    with pytest.raises(NetworkError, match="doesn't match an original ROM supported by this project"):
        engine._match_version(SimpleNamespace(), fp, cfg, copy, log=lambda *_: None)


def test_generated_splat_image_uses_original_cartridge_hashes(tmp_path):
    (tmp_path / 'Makefile').write_text('VERSION ?= us\nBASEROM := baserom.$(VERSION).z64\n'
                                      'DECOMPRESSED_BASEROM := decompressed.$(VERSION).z64\n')
    originals = {v: hashlib.sha1(rom(letter)).hexdigest() for v, letter in [('us', b'U'), ('pal', b'P')]}
    (tmp_path / 'README.md').write_text(''.join(f'`baserom.{v}.z64`: `{digest}`\n'
                                                for v, digest in originals.items()))
    for v in originals:
        (tmp_path / f'decompressed.{v}.yaml').write_text(
            f'options:\n  platform: n64\n  target_path: decompressed.{v}.z64\n  sha1: {"f" * 40}\nsegments: []\n')
    fp = ForkProject(tmp_path)
    assert fp.splat_baseroms() == originals
    assert fp.needs('pal')['sha1'] == originals['pal']
    assert fp.needs('pal')['path'] == 'baserom.pal.z64'


def test_nested_splat_versions_only_accept_configs_used_by_make(tmp_path):
    (tmp_path / 'Makefile').write_text('BASENAME := demo\nVERSION ?= us\n'
                                      'SPLAT_YAML := config/$(VERSION)/$(BASENAME).$(VERSION).yaml\n'
                                      'extract:\n\tpython -m splat split $(SPLAT_YAML)\n')
    originals = {v: rom(letter) for v, letter in [('us', b'U'), ('jp', b'J')]}
    for version, data in originals.items():
        config = tmp_path / 'config' / version / f'demo.{version}.yaml'
        config.parent.mkdir(parents=True)
        config.write_text(f'sha1: {hashlib.sha1(data).hexdigest()}\noptions:\n'
                          f'  platform: n64\n  base_path: ../..\n  target_path: baserom.{version}.z64\nsegments: []\n')
    fp = ForkProject(tmp_path)
    assert fp.splat_default_version() == 'us'
    assert fp.needs('jp')['path'] == 'baserom.jp.z64'
    assert fp.setup_commands('jp')[0] == ['make', 'VERSION=jp', 'extract']
    invalid = tmp_path / 'config' / 'jp' / 'demo.jp.yaml'
    invalid.write_text(invalid.read_text().replace('base_path: ../..', 'base_path: ../../..'))
    assert fp.splat_baseroms() == {}


def test_other_console_does_not_infer_n64_versions_from_z64_filename(tmp_path):
    fp, _ = project(tmp_path)
    config = tmp_path / 'game.us.yaml'
    config.write_text(config.read_text().replace('platform: n64', 'platform: ps'))
    config = tmp_path / 'game.pal.yaml'
    config.write_text(config.read_text().replace('platform: n64', 'platform: ps'))
    assert fp.detect_platform() == 'ps'
    fp.platform = fp.detect_platform()
    assert [console.id for console in fp._game_consoles()] == ['ps']
    assert fp.splat_baseroms() == {}
