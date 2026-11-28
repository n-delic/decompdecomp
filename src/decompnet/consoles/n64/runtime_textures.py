"""Observe display-list texture loads in a bounded, isolated emulator run."""
import hashlib
import tempfile
from pathlib import Path

from . import emulator, loaded_code

VIS = (1200, 2400, 4000, 5000)


def observe(image, rom, expected_sha1, *, log=lambda _: None, progress=lambda _: None, status=None, context=None):
    """Return assets backed by captured RAM and unique bytes in the analysis image."""
    from decompassets.n64 import runtime_audio, runtime_packed_graphics, runtime_ptr_audio, runtime_textures

    from ...network import NetworkError

    scene = (context or {}).get('runtime_scene')
    status = status if status is not None else {}
    status['state'] = 'unavailable'
    if not emulator.installed() or not expected_sha1:
        if scene:
            raise NetworkError('Restore the recorded game image and install the reference emulator before exploring a later scene.')
        log("Runtime texture observation is unavailable without the emulator and a recorded game hash.")
        return []
    hashes = [expected_sha1] if isinstance(expected_sha1, str) else expected_sha1
    rom = Path(rom)
    status['state'] = 'failed'
    try:
        with rom.open('rb') as stream:
            if stream.read(4) != bytes.fromhex('80371240'):
                if scene:
                    raise NetworkError('Restore the recorded N64 cartridge image before exploring a later scene.')
                status['state'] = 'unavailable'
                return []
            stream.seek(0)
            original = stream.read()
            digest = hashlib.sha1(original).hexdigest()
        if digest not in {value.lower() for value in hashes if isinstance(value, str)}:
            if scene:
                raise NetworkError('The game image no longer matches the recorded hash. Restore the original before exploring a later scene.')
            log('Runtime texture observation skipped: the original game no longer matches its recorded hash.')
            return []
        # Runtime libmus sample pointers name cartridge offsets. A transformed
        # analysis image is safe only when its original prefix is unchanged.
        raw_samples = len(image) >= len(original) and image[:len(original)] == original
        log('Observing texture loads in the reversing emulator; the game uses an isolated save folder.')
        progress(dict(phase='Observing runtime textures', done=0, total=1))
        with tempfile.TemporaryDirectory(prefix='decomp-runtime-textures-') as folder:
            dumps = {vi: Path(folder)/f'{vi}.ram' for vi in VIS}
            run, expansion = emulator.run_for_observation(rom, VIS[-1], dumps=dumps, timeout=120, log=log)
            snapshots = [path.read_bytes() for path in dumps.values() if path.is_file()]
            direct = runtime_textures.regions(image, snapshots)
            packed = runtime_packed_graphics.regions(image, snapshots, progress=progress)
            sounds = runtime_audio.regions(image, snapshots) + (runtime_ptr_audio.regions(image, snapshots) if raw_samples else [])
            if not run.timed_out:
                log('Observing title and gameplay textures with scripted input.')
                for path in dumps.values():
                    path.unlink(missing_ok=True)
                run = emulator.run(rom, VIS[-1], inputs=loaded_code.inputs(VIS[-1]), dumps=dumps,
                                   timeout=120, expansion_pak=expansion)
                snapshots = [path.read_bytes() for path in dumps.values() if path.is_file()]
                direct += runtime_textures.regions(image, snapshots)
                packed += runtime_packed_graphics.regions(image, snapshots, progress=progress)
                sounds += runtime_audio.regions(image, snapshots) + (runtime_ptr_audio.regions(image, snapshots) if raw_samples else [])
            # Some boot menus default to retrying an unavailable save device.
            # A second, bounded input path selects the next option before trying
            # the usual title buttons. It uses the same isolated save as above.
            if not run.timed_out and not direct and not any(a.get('textures') for a in packed):
                log('No textures were observed; trying an alternate menu selection.')
                for path in dumps.values():
                    path.unlink(missing_ok=True)
                inputs = '400:DOWN,408:,430:A,438:,' + ','.join(
                    f'{vi}:START,{vi+8}:,{vi+60}:A,{vi+68}:' for vi in range(600, VIS[-1], 120))
                run = emulator.run(rom, VIS[-1], inputs=inputs, dumps=dumps,
                                   timeout=120, expansion_pak=expansion)
                snapshots = [path.read_bytes() for path in dumps.values() if path.is_file()]
                direct += runtime_textures.regions(image, snapshots)
                packed += runtime_packed_graphics.regions(image, snapshots, progress=progress)
                sounds += runtime_audio.regions(image, snapshots) + (runtime_ptr_audio.regions(image, snapshots) if raw_samples else [])
            # Some games display a memory-expansion warning using the base RAM
            # rather than stalling before video. A successful retrace alone does
            # not show that their title or gameplay ran. Retry without changing
            # the user's save when neither run yielded a texture.
            expansion_failed = False
            if not run.timed_out and not expansion and not direct and not any(a.get('textures') for a in packed):
                log('No textures were observed; checking whether the game needs expanded memory.')
                try:
                    for inputs in ('', loaded_code.inputs(VIS[-1])):
                        for path in dumps.values():
                            path.unlink(missing_ok=True)
                        expanded = emulator.run(rom, VIS[-1], inputs=inputs, dumps=dumps,
                                                timeout=120, expansion_pak=True)
                        snapshots = [path.read_bytes() for path in dumps.values() if path.is_file()]
                        direct += runtime_textures.regions(image, snapshots)
                        packed += runtime_packed_graphics.regions(image, snapshots, progress=progress)
                        sounds += runtime_audio.regions(image, snapshots) + (runtime_ptr_audio.regions(image, snapshots) if raw_samples else [])
                        if expanded.timed_out:
                            expansion_failed = True
                            log('Expanded-memory observation timed out; completed snapshots are still included.')
                            break
                except emulator.EmulatorError as error:
                    expansion_failed = True
                    log(f'Expanded-memory observation skipped: {error}')
            if scene:
                log('Observing the requested later scene with the controller script.')
                progress(dict(phase='Observing the requested scene',done=0,total=1))
                end = scene['vis']
                late = {vi:Path(folder)/f'scene-{vi}.ram' for vi in (end//2,3*end//4,end)}
                try:
                    extra = emulator.run(rom,end,inputs=scene['inputs'],dumps=late,timeout=180,expansion_pak=expansion)
                except emulator.EmulatorError as error:
                    raise NetworkError(f'The later-scene observation failed: {error}. Try the controller sequence again.') from error
                captures = [path.read_bytes() for path in late.values() if path.is_file()]
                if extra.timed_out or len(captures) != len(late):
                    raise NetworkError('The later-scene observation did not finish. Try a shorter duration or sequence.')
                snapshots += captures
                direct += runtime_textures.regions(image,captures)
                packed += runtime_packed_graphics.regions(image,captures,progress=progress)
                sounds += runtime_audio.regions(image,captures) + (runtime_ptr_audio.regions(image,captures) if raw_samples else [])
            # A self-indexed compressed sprite bank and its independently
            # identified loader justify one longer, isolated menu observation.
            # Some games do not populate the sprite buffer until after setup.
            sprites = []
            if context:
                from decompassets.n64 import runtime_rle_sprites
                check = getattr(log, 'check_cancel', lambda: None)
                if next(runtime_rle_sprites.tables(image, check), None) and runtime_rle_sprites.evidence(image, context, check):
                    sprites = runtime_rle_sprites.regions(image, snapshots, context, check=check)
                    if not sprites and not run.timed_out:
                        log('Checking a compressed sprite bank after a longer menu sequence.')
                        late = {vi: Path(folder)/f'sprite-{vi}.ram' for vi in (12000, 16000)}
                        inputs = ('600:START,608:,1200:START,1208:,1800:START,1808:,2400:START,2408:,'
                                  '3000:START,3008:,5000:A,5008:,5200:START,5208:,7000:A,7008:,'
                                  '8500:A,8508:,10000:START,10008:,15500:A,15508:')
                        try:
                            extended = emulator.run(rom, 16000, inputs=inputs, dumps=late,
                                                    timeout=120, expansion_pak=expansion)
                            captures = [path.read_bytes() for path in late.values() if path.is_file()]
                            sprites = runtime_rle_sprites.regions(image, captures, context, check=check)
                            if extended.timed_out:
                                expansion_failed = True
                                log('Extended sprite observation timed out; completed snapshots are still included.')
                        except emulator.EmulatorError as error:
                            expansion_failed = True
                            log(f'Extended sprite observation skipped: {error}')
            # Indexed compressed archives can contain display lists that are
            # first reached after the normal short title observation.
            from decompassets.n64 import runtime_burp_models
            models = []
            check = getattr(log, 'check_cancel', lambda: None)
            if next(runtime_burp_models.archives(image, check), None):
                models = runtime_burp_models.regions(image, snapshots, check=check)
                if not run.timed_out:
                    log('Checking indexed model resources after a longer menu sequence.')
                    late = {vi: Path(folder)/f'model-{vi}.ram' for vi in (6000, 8000)}
                    inputs = ('600:START,608:,1200:START,1208:,1800:START,1808:,2400:START,2408:,'
                              '3000:A,3008:,4500:A,4508:,6000:START,6008:')
                    try:
                        extended = emulator.run(rom, 8000, inputs=inputs, dumps=late,
                                                timeout=120, expansion_pak=expansion)
                        captures = [path.read_bytes() for path in late.values() if path.is_file()]
                        models += runtime_burp_models.regions(image, captures, check=check)
                        if extended.timed_out:
                            expansion_failed = True
                            log('Extended model observation timed out; completed snapshots are still included.')
                    except emulator.EmulatorError as error:
                        expansion_failed = True
                        log(f'Extended model observation skipped: {error}')
            model_banks = {}
            for model in models:
                key = model['offset'], model['size']
                if key not in model_banks:
                    model_banks[key] = model
                    continue
                bank = model_banks[key]
                known = {(mesh['offset'], mesh['size'], mesh['slot']) for mesh in bank['meshes']}
                for mesh in model['meshes']:
                    identity = mesh['offset'], mesh['size'], mesh['slot']
                    if identity not in known and len(bank['meshes']) < 512:
                        bank['meshes'].append(dict(mesh, name=f"Mesh {len(bank['meshes'])+1}"))
                        known.add(identity)
                bank['vertices'] = sum(mesh['vertices'] for mesh in bank['meshes'])
                bank['triangles'] = sum(mesh['triangles'] for mesh in bank['meshes'])
            # Only inspect the longer menu path when an indexed image and its
            # palette are present; a complete captured frame is still required.
            from decompassets.n64 import indexed_bitstream_images
            indexed_images = []
            if any(any(kind == 2 and size == 320*240 and name.lower().endswith('.pic')
                       and any(pkind == 1 and pname.lower() == name[:-4].lower()+'.pal' and psize == 1052
                               for pname, pkind, _, psize in files)
                       for name, kind, _, size in files)
                   for _, files in indexed_bitstream_images.directories(image, check)):
                indexed_images = indexed_bitstream_images.regions(image, snapshots, check=check)
                if not indexed_images and not run.timed_out:
                    log('Checking indexed images after a longer menu sequence.')
                    late = {vi: Path(folder)/f'image-{vi}.ram' for vi in (5000, 8000)}
                    inputs = ('600:START,608:,1200:START,1208:,1800:START,1808:,2400:START,2408:,'
                              '3000:A,3008:,4500:A,4508:,6000:START,6008:')
                    try:
                        extended = emulator.run(rom, 8000, inputs=inputs, dumps=late,
                                                timeout=120, expansion_pak=expansion)
                        captures = [path.read_bytes() for path in late.values() if path.is_file()]
                        indexed_images = indexed_bitstream_images.regions(image, captures, check=check)
                        if extended.timed_out:
                            expansion_failed = True
                            log('Extended image observation timed out; completed snapshots are still included.')
                    except emulator.EmulatorError as error:
                        expansion_failed = True
                        log(f'Extended image observation skipped: {error}')
            # The submitted task, not an arbitrary later retrace, supplies the
            # palette: the game may reuse its memory before the next snapshot.
            from decompassets.n64 import (
                runtime_deflate_ci8,
                runtime_lzw_textures,
                runtime_variable_lz,
                runtime_variable_lz_models,
            )
            frame_assets = []
            recognizers = [module for module in (runtime_lzw_textures, runtime_deflate_ci8, runtime_variable_lz, runtime_variable_lz_models)
                           if module.has_resources(image, check)]
            if recognizers and not run.timed_out:
                log('Checking submitted graphics frames for compressed textures.')
                for inputs in ('', loaded_code.inputs(VIS[-1])):
                    frames = {n: Path(folder)/f'lzw-{n}.ram' for n in (500, 1000, 1400, 1800, 2200)}
                    for path in frames.values():
                        path.unlink(missing_ok=True)
                        Path(str(path)+'.task').unlink(missing_ok=True)
                    try:
                        captured = emulator.run(rom, VIS[-1], inputs=inputs, frame_dumps=frames,
                                                timeout=120, expansion_pak=expansion)
                        pairs = [(path.read_bytes(), Path(str(path)+'.task').read_bytes())
                                 for path in captured.frame_dumps.values()
                                 if path.is_file() and Path(str(path)+'.task').is_file()]
                        for module in recognizers:
                            frame_assets += module.regions(image, pairs, check=check)
                        if captured.timed_out:
                            expansion_failed = True
                            log('Frame observation timed out; completed frames are still included.')
                            break
                    except emulator.EmulatorError as error:
                        expansion_failed = True
                        log(f'Frame observation skipped: {error}')
                        break
            merged = {}
            for asset in packed + frame_assets:
                key = asset['offset'], asset.get('layout')
                if key in merged and asset.get('textures'):
                    previous = merged[key]['textures']
                    def texture_key(item):
                        return tuple(item['preview_settings'].items()), item.get('palette')
                    known = {texture_key(item) for item in previous}
                    for item in asset['textures']:
                        marker = texture_key(item)
                        if marker not in known:
                            previous.append(item)
                            known.add(marker)
                    if 'texture_count' in merged[key]:
                        merged[key]['texture_count'] = len(previous)
                elif key in merged and asset.get('layout') in ('runtime-zip-model', 'runtime-erz-model', 'runtime-edl-model', 'runtime-deflate-model', 'runtime-chunked-model', 'runtime-zlib-model', 'runtime-packed-model', 'runtime-variable-lz-model'):
                    previous = merged[key]['meshes']
                    def identity(item, layout=asset['layout']):
                        if layout in ('runtime-zip-model', 'runtime-erz-model'):
                            return item['vertex_offset'], item['commands']
                        if layout == 'runtime-edl-model':
                            return tuple(item['source']), tuple(tuple(face) for face in item['faces'])
                        if layout == 'runtime-chunked-model':
                            return item['vertex'], item.get('captured', (item['command'], item.get('command_size'))) 
                        if layout in ('runtime-zlib-model', 'runtime-packed-model', 'runtime-variable-lz-model'):
                            return item['commands'], item['base']
                        return item['offset'], item['size']
                    known = {identity(item) for item in previous}
                    for item in asset['meshes']:
                        marker = identity(item)
                        if marker not in known and len(previous) < 512:
                            previous.append(dict(item, name=f'Mesh {len(previous)+1}'))
                            known.add(marker)
                    merged[key]['vertices'] = sum(item['vertices'] for item in previous)
                    merged[key]['triangles'] = sum(item['triangles'] for item in previous)
                else:
                    merged[key] = asset
            # The idle and scripted runs may see the same texture. Preserve
            # distinct captured palettes while avoiding duplicate map regions.
            unique = {}
            for asset in direct:
                key = (asset.get('offset'), asset.get('size'), asset.get('layout'),
                       asset.get('width'), asset.get('height'), asset.get('format'),
                       asset.get('palette_offset'), asset.get('palette_little_endian'), asset.get('palette'),
                       asset.get('palette_format'), asset.get('odd_row_swap'))
                unique[key] = asset
            # A bank may appear in several runs. Conflicting observed bases are
            # ambiguous and must not be promoted to playable samples.
            bases = {}
            for asset in sounds:
                key = asset['bank_offset'], asset.get('layout')
                bases.setdefault(key, set()).add(asset['table_offset'])
            audio = {(asset['bank_offset'], asset.get('layout'), asset['offset'], asset['size']): asset for asset in sounds
                     if len(bases[asset['bank_offset'], asset.get('layout')]) == 1}
            assets = list(unique.values()) + list(merged.values()) + list(audio.values()) + sprites + list(model_banks.values()) + indexed_images
        if run.timed_out:
            log('Runtime observation timed out; any completed snapshots are still included.')
        graphics = sum(a.get('kind') != 'audio' for a in assets)
        log(f'Found {graphics} graphics regions backed by runtime commands and cartridge bytes.')
        if audio:
            log(f'Found {len(audio)} audio regions backed by relocated runtime controls.')
        status['state'] = 'failed' if run.timed_out or expansion_failed else 'complete'
        progress(dict(phase='Observing runtime textures', done=1, total=1))
        return assets
    except (OSError, emulator.EmulatorError) as error:
        if scene:
            raise NetworkError(f'The later-scene observation could not start: {error}. Check the game image and emulator.') from error
        log(f'Runtime texture observation skipped: {error}')
        return []
