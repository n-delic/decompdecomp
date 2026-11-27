from hashlib import sha1
from types import SimpleNamespace

import pytest

from decompassets.n64 import runtime_textures as detector
from decompnet.consoles.n64 import runtime_textures


def test_observation_requires_original_and_uses_isolated_snapshots(tmp_path, monkeypatch):
    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    calls = []
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, timeout, **kwargs):
        calls.append((path, vis, timeout))
        for dump in dumps.values():
            dump.write_bytes(b'ram')
        return SimpleNamespace(timed_out=False)

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [dict(offset=1, size=1, layout='test')])
    status = {}
    assert runtime_textures.observe(b'analysis', rom, '0' * 40, status=status) == []
    assert status['state'] == 'failed'
    assert not calls
    assert runtime_textures.observe(b'analysis', rom, sha1(rom.read_bytes()).hexdigest(), status=status) == [dict(offset=1, size=1, layout='test')]
    assert status['state'] == 'complete'
    assert calls == [(rom, runtime_textures.VIS[-1], 120)] * 2
    assert runtime_textures.observe(b'analysis', rom, ['0' * 40, sha1(rom.read_bytes()).hexdigest()]) == [dict(offset=1, size=1, layout='test')]


def test_observation_is_optional_and_reports_failure(tmp_path, monkeypatch):
    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: None)
    assert runtime_textures.observe(b'', rom, sha1(rom.read_bytes()).hexdigest()) == []
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    monkeypatch.setattr(runtime_textures.emulator, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(runtime_textures.emulator.EmulatorError('unavailable')))
    logs = []
    assert runtime_textures.observe(b'', rom, sha1(rom.read_bytes()).hexdigest(), log=logs.append) == []
    assert any('unavailable' in line for line in logs)


def test_observation_retries_a_boot_stuck_before_video_with_expansion(tmp_path, monkeypatch):
    emulator = runtime_textures.emulator
    dump = tmp_path / 'ram'
    calls = []

    def run(rom, vis, **kwargs):
        calls.append(kwargs.get('expansion_pak', False))
        if not kwargs.get('expansion_pak'):
            dump.write_bytes(b'stale')
            raise emulator.EmulatorStuckError('stuck', 0)
        assert not dump.exists()
        dump.write_bytes(b'expanded ram')
        return emulator.Run(vis=vis, dumps={vis: dump})

    monkeypatch.setattr(emulator, 'run', run)
    result, expanded = emulator.run_for_observation(tmp_path / 'game', 10, dumps={10: dump}, log=lambda _: None)
    assert expanded and result.dumps[10].read_bytes() == b'expanded ram'
    assert calls == [False, True]


def test_observation_does_not_retry_errors_after_video(tmp_path, monkeypatch):
    emulator = runtime_textures.emulator
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        raise emulator.EmulatorStuckError('stuck', 12)

    monkeypatch.setattr(emulator, 'run', run)
    with pytest.raises(emulator.EmulatorStuckError):
        emulator.run_for_observation(tmp_path / 'game', 100)
    assert len(calls) == 1


def test_observation_retries_timeout_only_before_any_video(tmp_path, monkeypatch):
    emulator = runtime_textures.emulator
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs.get('expansion_pak', False))
        return emulator.Run(vis=100 if calls[-1] else 0, timed_out=not calls[-1])

    monkeypatch.setattr(emulator, 'run', run)
    result, expanded = emulator.run_for_observation(tmp_path / 'game', 100, log=lambda _: None)
    assert expanded and result.vis == 100 and calls == [False, True]


def test_observation_tries_menu_input_if_no_texture_is_visible(tmp_path, monkeypatch):
    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    calls = []

    def run(path, vis, *, dumps, **kwargs):
        calls.append(kwargs)
        for dump in dumps.values():
            dump.write_bytes(b'menus' if kwargs.get('inputs') else b'title')
        return runtime_textures.emulator.Run(vis=vis)

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [dict(offset=1, size=1, layout='test')] if b'menus' in snapshots else [])
    assert runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest()) == [dict(offset=1, size=1, layout='test')]
    assert len(calls) == 2 and calls[1]['inputs'] and not calls[1]['expansion_pak']


def test_packed_title_assets_do_not_skip_gameplay_and_merge_banks(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    calls = []

    def run(path, vis, *, dumps, **kwargs):
        calls.append(kwargs)
        for dump in dumps.values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'title')
        return runtime_textures.emulator.Run(vis=vis)

    def packed(image, snapshots, *, progress):
        value = 2 if b'game' in snapshots else 1
        return [dict(offset=100, layout='packed', textures=[dict(preview_settings=dict(offset=value))])]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', packed)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    assert len(calls) == 2 and len(assets) == 1
    assert [t['preview_settings']['offset'] for t in assets[0]['textures']] == [1, 2]


def test_idle_textures_do_not_prevent_scripted_gameplay_capture(tmp_path, monkeypatch):
    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    calls = []

    def run(path, vis, *, dumps, **kwargs):
        calls.append(kwargs)
        for dump in dumps.values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'idle')
        return runtime_textures.emulator.Run(vis=vis)

    def scan(image, snapshots):
        same = dict(offset=100, size=16, layout='direct')
        return [same, dict(offset=200, size=16, layout='game')] if b'game' in snapshots else [same]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', scan)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    assert len(calls) == 2 and calls[1]['inputs']
    assert [asset['offset'] for asset in assets] == [100, 200]
