from hashlib import sha1
from types import SimpleNamespace

from decompassets.n64 import runtime_textures as detector
from decompnet.consoles.n64 import runtime_textures


def test_observation_requires_original_and_uses_isolated_snapshots(tmp_path, monkeypatch):
    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    calls = []
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, timeout):
        calls.append((path, vis, timeout))
        for dump in dumps.values():
            dump.write_bytes(b'ram')
        return SimpleNamespace(timed_out=False)

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: list(snapshots))
    status = {}
    assert runtime_textures.observe(b'analysis', rom, '0' * 40, status=status) == []
    assert status['state'] == 'failed'
    assert not calls
    assert runtime_textures.observe(b'analysis', rom, sha1(rom.read_bytes()).hexdigest(), status=status) == [b'ram'] * 4
    assert status['state'] == 'complete'
    assert calls == [(rom, runtime_textures.VIS[-1], 120)]
    assert runtime_textures.observe(b'analysis', rom, ['0' * 40, sha1(rom.read_bytes()).hexdigest()]) == [b'ram'] * 4


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
