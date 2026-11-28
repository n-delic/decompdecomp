from hashlib import sha1
from pathlib import Path
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


def test_requested_scene_adds_later_textures_and_merges_loaded_meshes(tmp_path, monkeypatch):
    from decompassets.n64 import (
        indexed_bitstream_images,
        runtime_audio,
        runtime_burp_models,
        runtime_packed_graphics,
        runtime_ptr_audio,
        runtime_rle_sprites,
    )

    rom = tmp_path/'game.z64'
    rom.write_bytes(bytes.fromhex('80371240')+b'original')
    calls = []
    monkeypatch.setattr(runtime_textures.emulator,'installed',lambda:tmp_path)
    def run(path,vis,*,dumps,timeout,**kwargs):
        calls.append((vis,kwargs.get('inputs','')))
        for dump in dumps.values():dump.write_bytes(b'later' if vis==12000 else b'opening')
        return SimpleNamespace(timed_out=False)
    monkeypatch.setattr(runtime_textures.emulator,'run',run)
    monkeypatch.setattr(detector,'regions',lambda image,snapshots:[dict(offset=2 if b'later' in snapshots else 1,size=1,layout='test')])
    def packed(image,snapshots,**kwargs):
        name = 'later' if b'later' in snapshots else 'opening'
        return [dict(offset=7,layout='runtime-packed-model',meshes=[dict(commands=name,base=0,vertices=4,triangles=2)])]
    monkeypatch.setattr(runtime_packed_graphics,'regions',packed)
    monkeypatch.setattr(runtime_audio,'regions',lambda *args:[])
    monkeypatch.setattr(runtime_ptr_audio,'regions',lambda *args:[])
    monkeypatch.setattr(runtime_rle_sprites,'tables',lambda *args:iter(()))
    monkeypatch.setattr(runtime_burp_models,'archives',lambda *args:iter(()))
    monkeypatch.setattr(indexed_bitstream_images,'directories',lambda *args:[])
    result = runtime_textures.observe(rom.read_bytes(),rom,sha1(rom.read_bytes()).hexdigest(),
        context={'runtime_scene':dict(vis=12000,inputs='600:START,608:')})
    assert {a['offset'] for a in result} == {1,2,7}
    model = next(a for a in result if a['offset']==7)
    assert {m['commands'] for m in model['meshes']} == {'opening','later'}
    assert calls[-1] == (12000,'600:START,608:')


def test_requested_scene_timeout_is_reported_instead_of_publishing_an_incomplete_scan(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_audio, runtime_packed_graphics, runtime_ptr_audio
    from decompnet.network import NetworkError

    rom=tmp_path/'game.z64';rom.write_bytes(bytes.fromhex('80371240')+b'original')
    monkeypatch.setattr(runtime_textures.emulator,'installed',lambda:tmp_path)
    def run(path,vis,*,dumps,timeout,**kwargs):
        if vis != 12000:
            for dump in dumps.values():dump.write_bytes(b'opening')
        return SimpleNamespace(timed_out=vis==12000)
    monkeypatch.setattr(runtime_textures.emulator,'run',run)
    monkeypatch.setattr(detector,'regions',lambda *args:[dict(offset=1,size=1,layout='test')])
    monkeypatch.setattr(runtime_packed_graphics,'regions',lambda *args,**kwargs:[])
    monkeypatch.setattr(runtime_audio,'regions',lambda *args:[])
    monkeypatch.setattr(runtime_ptr_audio,'regions',lambda *args:[])
    with pytest.raises(NetworkError,match='shorter duration'):
        runtime_textures.observe(rom.read_bytes(),rom,sha1(rom.read_bytes()).hexdigest(),
            context={'runtime_scene':dict(vis=12000,inputs='600:START,608:')})


def test_requested_scene_requires_the_recorded_image(tmp_path,monkeypatch):
    from decompnet.network import NetworkError

    rom=tmp_path/'game.z64';rom.write_bytes(bytes.fromhex('80371240')+b'original')
    scene={'runtime_scene':dict(vis=60,inputs='1:A')}
    monkeypatch.setattr(runtime_textures.emulator,'installed',lambda:tmp_path)
    with pytest.raises(NetworkError,match='recorded hash'):
        runtime_textures.observe(rom.read_bytes(),rom,'0'*40,context=scene)
    with pytest.raises(NetworkError,match='recorded game image'):
        runtime_textures.observe(rom.read_bytes(),rom,None,context=scene)


def test_observation_keeps_distinct_tlut_formats(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, timeout, **kwargs):
        for dump in dumps.values():
            dump.write_bytes(b'ram')
        return SimpleNamespace(timed_out=False)

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(runtime_packed_graphics, 'regions', lambda *args, **kwargs: [])
    rows = [dict(offset=1, size=1, layout='test', palette='abcd', palette_format=fmt)
            for fmt in ('ia16', 'rgba5551-be')]
    rows.extend([dict(offset=2, size=1, layout='test', odd_row_swap=swap) for swap in (0, 8)])
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: list(rows))
    assert runtime_textures.observe(b'analysis', rom, sha1(rom.read_bytes()).hexdigest()) == rows


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


@pytest.mark.parametrize('layout', ['runtime-zip-model', 'runtime-erz-model'])
def test_archive_mesh_batches_merge_between_runtime_captures(tmp_path, monkeypatch, layout):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, **kwargs):
        for dump in dumps.values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'title')
        return runtime_textures.emulator.Run(vis=vis)

    def packed(image, snapshots, *, progress):
        first = dict(name='Mesh 1', vertex_offset=32, commands='a', vertices=4, triangles=3)
        second = dict(name='Mesh 2', vertex_offset=96, commands='b', vertices=5, triangles=4)
        meshes = [first, second] if b'game' in snapshots else [first]
        return [dict(offset=100, layout=layout, meshes=meshes,
                     vertices=sum(m['vertices'] for m in meshes), triangles=sum(m['triangles'] for m in meshes))]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [dict(offset=1, size=1, layout='texture')])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', packed)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    model = next(asset for asset in assets if asset['layout'] == layout)
    assert [mesh['vertex_offset'] for mesh in model['meshes']] == [32, 96]
    assert (model['vertices'], model['triangles']) == (9, 7)


def test_deflate_meshes_merge_between_runtime_captures(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, **kwargs):
        for dump in dumps.values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'title')
        return runtime_textures.emulator.Run(vis=vis)

    def packed(image, snapshots, *, progress):
        first = dict(name='Mesh 1', offset=32, size=24, vertices=4, triangles=3)
        second = dict(name='Mesh 2', offset=96, size=32, vertices=5, triangles=4)
        meshes = [first, second] if snapshots[0] == b'game' else [first]
        return [dict(offset=100, layout='runtime-deflate-model', meshes=meshes,
                     vertices=sum(m['vertices'] for m in meshes), triangles=sum(m['triangles'] for m in meshes))]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [dict(offset=1, size=1, layout='texture')])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', packed)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    model = next(asset for asset in assets if asset['layout'] == 'runtime-deflate-model')
    assert [mesh['offset'] for mesh in model['meshes']] == [32, 96]
    assert (model['vertices'], model['triangles']) == (9, 7)


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


def test_expansion_retry_after_a_video_warning_screen(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    calls = []

    def run(path, vis, *, dumps, **kwargs):
        calls.append(kwargs)
        for dump in dumps.values():
            dump.write_bytes(b'expansion' if kwargs.get('expansion_pak') else b'warning')
        return runtime_textures.emulator.Run(vis=vis)

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots:
                        [dict(offset=200, size=16, layout='texture')] if b'expansion' in snapshots else [])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', lambda image, snapshots, *, progress:
                        [dict(offset=100, layout='model')])
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    assert [c.get('expansion_pak', False) for c in calls] == [False, False, False, True, True]
    assert calls[1]['inputs'] and calls[2]['inputs'].startswith('400:DOWN') and calls[4]['inputs']
    assert {a['offset'] for a in assets} == {100, 200}


def test_failed_expansion_retry_keeps_existing_models_and_can_retry(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, **kwargs):
        if kwargs.get('expansion_pak'):
            raise runtime_textures.emulator.EmulatorError('failed')
        for dump in dumps.values():
            dump.write_bytes(b'warning')
        return runtime_textures.emulator.Run(vis=vis)

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', lambda image, snapshots, *, progress:
                        [dict(offset=100, layout='model')])
    status = {}
    assert runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest(), status=status) == [
        dict(offset=100, layout='model')]
    assert status['state'] == 'failed'


def test_chunked_meshes_merge_between_runtime_captures(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, **kwargs):
        for dump in dumps.values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'title')
        return runtime_textures.emulator.Run(vis=vis)

    def packed(image, snapshots, *, progress):
        first = dict(name='Mesh 1', command=32, vertex=64, vertices=4, triangles=3)
        second = dict(name='Mesh 2', command=96, vertex=128, vertices=5, triangles=4)
        meshes = [first, second] if snapshots[0] == b'game' else [first]
        return [dict(offset=100, layout='runtime-chunked-model', meshes=meshes,
                     vertices=sum(m['vertices'] for m in meshes), triangles=sum(m['triangles'] for m in meshes))]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [dict(offset=1, size=1, layout='texture')])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', packed)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    model = next(asset for asset in assets if asset['layout'] == 'runtime-chunked-model')
    assert [mesh['command'] for mesh in model['meshes']] == [32, 96]
    assert (model['vertices'], model['triangles']) == (9, 7)


def test_chunked_meshes_keep_distinct_runtime_commands_for_the_same_vertices(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, **kwargs):
        for dump in dumps.values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'title')
        return runtime_textures.emulator.Run(vis=vis)

    def packed(image, snapshots, *, progress):
        first = dict(name='Mesh 1', command=-1, vertex=64, captured='aa', vertices=4, triangles=3)
        second = dict(name='Mesh 2', command=-1, vertex=64, captured='bb', vertices=4, triangles=4)
        meshes = [first, second] if snapshots[0] == b'game' else [first]
        return [dict(offset=100, layout='runtime-chunked-model', meshes=meshes,
                     vertices=sum(m['vertices'] for m in meshes), triangles=sum(m['triangles'] for m in meshes))]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [dict(offset=1, size=1, layout='texture')])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', packed)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    model = next(asset for asset in assets if asset['layout'] == 'runtime-chunked-model')
    assert [mesh['captured'] for mesh in model['meshes']] == ['aa', 'bb']


@pytest.mark.parametrize('conflicting', [False, True])
def test_observed_audio_is_deduplicated_and_conflicting_tables_are_rejected(tmp_path, monkeypatch, conflicting):
    from decompassets.n64 import runtime_audio

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, **kwargs):
        for dump in dumps.values():
            dump.write_bytes(b'menu' if kwargs.get('inputs') else b'title')
        return SimpleNamespace(timed_out=False)

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda *_: [dict(offset=1, size=1, layout='test')])
    def sounds(image, snapshots):
        base = 300 if conflicting and b'menu' in snapshots else 200
        return [dict(kind='audio', audio_kind='sample bank', bank_offset=100, table_offset=base,
                     offset=base, size=10)]
    monkeypatch.setattr(runtime_audio, 'regions', sounds)
    result = runtime_textures.observe(b'analysis', rom, sha1(rom.read_bytes()).hexdigest())
    assert len([a for a in result if a.get('kind') == 'audio']) == (0 if conflicting else 1)


def test_runtime_bank_replaces_unresolved_static_controls(monkeypatch):
    from decompassets import n64
    from decompnet.consoles.n64 import Nintendo64

    unresolved = dict(kind='audio', audio_kind='unresolved bank', offset=100)
    unrelated = dict(kind='audio', audio_kind='unresolved bank', offset=101)
    observed = dict(kind='audio', audio_kind='sample bank', bank_offset=100, table_offset=200, offset=100)
    other_sample = dict(kind='audio', audio_kind='sample bank', offset=201, table_offset=200)
    monkeypatch.setattr(n64, 'regions', lambda *args, **kwargs: [unresolved, unrelated, other_sample])
    monkeypatch.setattr(runtime_textures, 'observe', lambda *args, **kwargs: [observed])
    result = Nintendo64().asset_regions(b'image', context={'runtime_rom': 'game'})
    assert result == [unrelated, other_sample, observed]


def test_distinct_runtime_audio_formats_do_not_conflict(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_audio, runtime_ptr_audio

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    def run(path, vis, *, dumps, **kwargs):
        for dump in dumps.values():dump.write_bytes(b'ram')
        return SimpleNamespace(timed_out=False)
    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda *_: [dict(offset=1, size=1, layout='test')])
    def item(layout, table):
        return [dict(kind='audio', bank_offset=100, table_offset=table, offset=100, size=10, layout=layout)]
    monkeypatch.setattr(runtime_audio, 'regions', lambda *_: item('b1', 200))
    monkeypatch.setattr(runtime_ptr_audio, 'regions', lambda *_: item('libmus', 300))
    result = runtime_textures.observe(rom.read_bytes(), rom, sha1(rom.read_bytes()).hexdigest())
    assert {a['layout'] for a in result if a.get('kind') == 'audio'} == {'b1', 'libmus'}
    changed = runtime_textures.observe(b'changed', rom, sha1(rom.read_bytes()).hexdigest())
    assert {a['layout'] for a in changed if a.get('kind') == 'audio'} == {'b1'}


def test_alternate_menu_selection_exposes_textures_without_an_expansion_retry(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    calls = []

    def run(path, vis, *, dumps, **kwargs):
        calls.append(kwargs)
        for dump in dumps.values():
            dump.write_bytes(b'alternate' if kwargs.get('inputs', '').startswith('400:DOWN') else b'prompt')
        return runtime_textures.emulator.Run(vis=vis)

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots:
                        [dict(offset=100, size=16, layout='texture')] if b'alternate' in snapshots else [])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', lambda *args, **kwargs: [])
    result = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    assert [asset['offset'] for asset in result] == [100]
    assert len(calls) == 3 and calls[-1]['inputs'].startswith('400:DOWN')
    assert not any(call.get('expansion_pak') for call in calls)



def test_zlib_model_meshes_merge_between_runtime_captures(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, **kwargs):
        for dump in dumps.values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'title')
        return runtime_textures.emulator.Run(vis=vis)

    def packed(image, snapshots, *, progress):
        first = dict(name='Mesh 1', commands='aa', base=64, vertices=4, triangles=3)
        second = dict(name='Mesh 2', commands='bb', base=64, vertices=5, triangles=4)
        meshes = [first, second] if snapshots[0] == b'game' else [first]
        return [dict(offset=100, layout='runtime-zlib-model', meshes=meshes,
                     vertices=sum(m['vertices'] for m in meshes), triangles=sum(m['triangles'] for m in meshes))]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [dict(offset=1, size=1, layout='texture')])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', packed)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    model = next(asset for asset in assets if asset['layout'] == 'runtime-zlib-model')
    assert [mesh['commands'] for mesh in model['meshes']] == ['aa', 'bb']
    assert (model['vertices'], model['triangles']) == (9, 7)


def test_compressed_sprite_evidence_triggers_bounded_late_observation(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics, runtime_rle_sprites

    rom = tmp_path/'game.z64'
    rom.write_bytes(bytes.fromhex('80371240')+b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    calls = []
    def run(path, vis, *, dumps, **kwargs):
        calls.append((vis, kwargs))
        for dump in dumps.values():
            dump.write_bytes(b'late' if vis == 16000 else b'early')
        return runtime_textures.emulator.Run(vis=vis)
    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda *_: [dict(offset=1, size=1, layout='texture')])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', lambda *args, **kwargs: [])
    monkeypatch.setattr(runtime_rle_sprites, 'tables', lambda *args: iter([(1, 2, [0, 1])]))
    monkeypatch.setattr(runtime_rle_sprites, 'evidence', lambda *args: [(1, 2, 3, 4, b'data')])
    monkeypatch.setattr(runtime_rle_sprites, 'regions', lambda image, snapshots, context, **kwargs:
                        [dict(offset=100, size=16, layout='runtime-rle-sprite-bank')] if b'late' in snapshots else [])
    result = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest(), context={'read_code': True})
    assert {asset['offset'] for asset in result} == {1, 100}
    assert calls[-1][0] == 16000 and calls[-1][1]['timeout'] == 120
    assert ':START,' in calls[-1][1]['inputs'] and ':A,' in calls[-1][1]['inputs']


def test_indexed_model_archive_triggers_bounded_late_observation(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_burp_models, runtime_packed_graphics

    rom = tmp_path/'game.z64'
    rom.write_bytes(bytes.fromhex('80371240')+b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    calls = []
    def run(path, vis, *, dumps, **kwargs):
        calls.append((vis, kwargs))
        for dump in dumps.values():
            dump.write_bytes(b'late' if vis == 8000 else b'early')
        return runtime_textures.emulator.Run(vis=vis)
    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda *_: [dict(offset=1, size=1, layout='texture')])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', lambda *args, **kwargs: [])
    monkeypatch.setattr(runtime_burp_models, 'archives', lambda *args: iter([(1, [])]))
    monkeypatch.setattr(runtime_burp_models, 'regions', lambda image, snapshots, **kwargs:
                        [dict(offset=100, size=16, kind='3d model', layout='runtime-burp-model')]
                        if b'late' in snapshots else [])
    result = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    assert {asset['offset'] for asset in result} == {1, 100}
    assert calls[-1][0] == 8000 and calls[-1][1]['timeout'] == 120


def test_indexed_image_directory_triggers_bounded_observation(tmp_path, monkeypatch):
    from decompassets.n64 import indexed_bitstream_images, runtime_burp_models, runtime_packed_graphics

    rom = tmp_path/'game.z64'
    rom.write_bytes(bytes.fromhex('80371240')+b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    calls = []
    def run(path, vis, *, dumps, **kwargs):
        calls.append((vis, kwargs))
        for dump in dumps.values():
            dump.write_bytes(b'late' if vis == 8000 else b'early')
        return runtime_textures.emulator.Run(vis=vis)
    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda *_: [dict(offset=1, size=1, layout='texture')])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', lambda *args, **kwargs: [])
    monkeypatch.setattr(runtime_burp_models, 'archives', lambda *args: iter([]))
    monkeypatch.setattr(indexed_bitstream_images, 'directories', lambda *args: iter([
        (0, [('screen.pic', 2, 0, 320*240), ('screen.pal', 1, 1, 1052)])]))
    monkeypatch.setattr(indexed_bitstream_images, 'regions', lambda image, snapshots, **kwargs:
                        [dict(offset=100, size=16, kind='image', layout='indexed-bitstream-image')]
                        if b'late' in snapshots else [])
    result = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    assert {asset['offset'] for asset in result} == {1, 100}
    assert calls[-1][0] == 8000 and calls[-1][1]['timeout'] == 120


def test_edl_meshes_merge_between_runtime_captures(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)

    def run(path, vis, *, dumps, **kwargs):
        for dump in dumps.values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'title')
        return runtime_textures.emulator.Run(vis=vis)

    def packed(image, snapshots, *, progress):
        first = dict(name='Mesh 1', source=[1, 2, 3, 4], faces=[[0, 1, 2]], vertices=4, triangles=1)
        second = dict(name='Mesh 2', source=[5, 6, 7, 8], faces=[[0, 1, 2]], vertices=4, triangles=1)
        meshes = [first, second] if b'game' in snapshots else [first]
        return [dict(offset=100, layout='runtime-edl-model', meshes=meshes,
                     vertices=sum(m['vertices'] for m in meshes), triangles=sum(m['triangles'] for m in meshes))]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(detector, 'regions', lambda image, snapshots: [dict(offset=1, size=1, layout='texture')])
    monkeypatch.setattr(runtime_packed_graphics, 'regions', packed)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    model = next(asset for asset in assets if asset['layout'] == 'runtime-edl-model')
    assert [mesh['source'] for mesh in model['meshes']] == [[1, 2, 3, 4], [5, 6, 7, 8]]
    assert (model['vertices'], model['triangles']) == (8, 2)


@pytest.mark.parametrize(('module_name', 'layout'), [('runtime_lzw_textures', 'runtime-lzw-textures'),
                                                   ('runtime_deflate_ci8', 'runtime-deflate-ci8'),
                                                   ('runtime_variable_lz', 'runtime-variable-lz-ci4')])
def test_compressed_texture_observation_uses_submitted_tasks_and_keeps_palettes(tmp_path, monkeypatch, module_name, layout):
    from decompassets import n64
    from decompassets.n64 import runtime_packed_graphics

    recognizer = getattr(n64, module_name)

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    monkeypatch.setattr(recognizer, 'has_resources', lambda *_: True)
    monkeypatch.setattr(runtime_packed_graphics, 'regions', lambda *args, **kwargs: [])
    monkeypatch.setattr(detector, 'regions', lambda *_: [dict(offset=1, size=1, layout='texture')])
    calls = []

    def run(path, vis, *, dumps=None, frame_dumps=None, **kwargs):
        for dump in (dumps or {}).values():
            dump.write_bytes(b'ram')
        for dump in (frame_dumps or {}).values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'title')
            Path(str(dump)+'.task').write_bytes(b'task')
        calls.append(bool(frame_dumps))
        return runtime_textures.emulator.Run(vis=vis, frame_dumps=frame_dumps or {})

    def regions(image, frames, **kwargs):
        assert frames and all(task == b'task' for _, task in frames)
        palette = 'game' if frames[0][0] == b'game' else 'title'
        item = dict(preview_settings=dict(offset=3), palette=palette)
        return [dict(offset=100, size=16, kind='image', layout=layout,
                     textures=[item], texture_count=1)]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(recognizer, 'regions', regions)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    bank, = [asset for asset in assets if asset.get('layout') == layout]
    assert [item['palette'] for item in bank['textures']] == ['title', 'game']
    assert bank['texture_count'] == 2
    assert sum(calls) == 2


def test_compressed_model_observation_keeps_distinct_meshes(tmp_path, monkeypatch):
    from decompassets.n64 import runtime_packed_graphics, runtime_variable_lz_models

    rom = tmp_path / 'game.z64'
    rom.write_bytes(bytes.fromhex('80371240') + b'original')
    monkeypatch.setattr(runtime_textures.emulator, 'installed', lambda: tmp_path)
    monkeypatch.setattr(runtime_variable_lz_models, 'has_resources', lambda *_: True)
    monkeypatch.setattr(runtime_packed_graphics, 'regions', lambda *args, **kwargs: [])
    monkeypatch.setattr(detector, 'regions', lambda *_: [dict(offset=1, size=1, layout='texture')])

    def run(path, vis, *, dumps=None, frame_dumps=None, **kwargs):
        for dump in (dumps or {}).values():
            dump.write_bytes(b'ram')
        for dump in (frame_dumps or {}).values():
            dump.write_bytes(b'game' if kwargs.get('inputs') else b'title')
            Path(str(dump)+'.task').write_bytes(b'task')
        return runtime_textures.emulator.Run(vis=vis, frame_dumps=frame_dumps or {})

    def regions(image, frames, **kwargs):
        assert frames and all(task == b'task' for _, task in frames)
        commands = frames[0][0].decode()
        mesh = dict(commands=commands, base=0, vertices=4, triangles=3)
        return [dict(offset=100, size=16, kind='3d model', layout='runtime-variable-lz-model',
                     meshes=[mesh], vertices=4, triangles=3)]

    monkeypatch.setattr(runtime_textures.emulator, 'run', run)
    monkeypatch.setattr(runtime_variable_lz_models, 'regions', regions)
    assets = runtime_textures.observe(b'image', rom, sha1(rom.read_bytes()).hexdigest())
    bank, = [asset for asset in assets if asset.get('layout') == 'runtime-variable-lz-model']
    assert [item['commands'] for item in bank['meshes']] == ['title', 'game']
    assert (bank['vertices'], bank['triangles']) == (8, 6)
