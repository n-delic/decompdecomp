"""Observe display-list texture loads in a bounded, isolated emulator run."""
import hashlib
import tempfile
from pathlib import Path

from . import emulator, loaded_code

VIS = (1200, 2400, 4000, 5000)


def observe(image, rom, expected_sha1, *, log=lambda _: None, progress=lambda _: None, status=None):
    """Return assets backed by captured RAM and unique bytes in the analysis image."""
    from decompassets.n64 import runtime_audio, runtime_packed_graphics, runtime_textures

    status = status if status is not None else {}
    status['state'] = 'unavailable'
    if not emulator.installed() or not expected_sha1:
        log("Runtime texture observation is unavailable without the emulator and a recorded game hash.")
        return []
    hashes = [expected_sha1] if isinstance(expected_sha1, str) else expected_sha1
    rom = Path(rom)
    status['state'] = 'failed'
    try:
        with rom.open('rb') as stream:
            if stream.read(4) != bytes.fromhex('80371240'):
                status['state'] = 'unavailable'
                return []
            stream.seek(0)
            digest = hashlib.file_digest(stream, 'sha1').hexdigest()
        if digest not in {value.lower() for value in hashes if isinstance(value, str)}:
            log('Runtime texture observation skipped: the original game no longer matches its recorded hash.')
            return []
        log('Observing texture loads in the reversing emulator; the game uses an isolated save folder.')
        progress(dict(phase='Observing runtime textures', done=0, total=1))
        with tempfile.TemporaryDirectory(prefix='decomp-runtime-textures-') as folder:
            dumps = {vi: Path(folder)/f'{vi}.ram' for vi in VIS}
            run, expansion = emulator.run_for_observation(rom, VIS[-1], dumps=dumps, timeout=120, log=log)
            snapshots = [path.read_bytes() for path in dumps.values() if path.is_file()]
            direct = runtime_textures.regions(image, snapshots)
            packed = runtime_packed_graphics.regions(image, snapshots, progress=progress)
            sounds = runtime_audio.regions(image, snapshots)
            if not run.timed_out:
                log('Observing title and gameplay textures with scripted input.')
                for path in dumps.values():
                    path.unlink(missing_ok=True)
                run = emulator.run(rom, VIS[-1], inputs=loaded_code.inputs(VIS[-1]), dumps=dumps,
                                   timeout=120, expansion_pak=expansion)
                snapshots = [path.read_bytes() for path in dumps.values() if path.is_file()]
                direct += runtime_textures.regions(image, snapshots)
                packed += runtime_packed_graphics.regions(image, snapshots, progress=progress)
                sounds += runtime_audio.regions(image, snapshots)
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
                        sounds += runtime_audio.regions(image, snapshots)
                        if expanded.timed_out:
                            expansion_failed = True
                            log('Expanded-memory observation timed out; completed snapshots are still included.')
                            break
                except emulator.EmulatorError as error:
                    expansion_failed = True
                    log(f'Expanded-memory observation skipped: {error}')
            merged = {}
            for asset in packed:
                key = asset['offset'], asset.get('layout')
                if key in merged and asset.get('textures'):
                    previous = merged[key]['textures']
                    known = {tuple(item['preview_settings'].items()) for item in previous}
                    previous.extend(item for item in asset['textures']
                                    if tuple(item['preview_settings'].items()) not in known)
                elif key in merged and asset.get('layout') in ('runtime-zip-model', 'runtime-deflate-model', 'runtime-chunked-model'):
                    previous = merged[key]['meshes']
                    def identity(item, layout=asset['layout']):
                        if layout == 'runtime-zip-model':
                            return item['vertex_offset'], item['commands']
                        if layout == 'runtime-chunked-model':
                            return item['vertex'], item.get('captured', (item['command'], item.get('command_size'))) 
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
                bases.setdefault(asset['bank_offset'], set()).add(asset['table_offset'])
            audio = {(asset['bank_offset'], asset['offset'], asset['size']): asset for asset in sounds
                     if len(bases[asset['bank_offset']]) == 1}
            assets = list(unique.values()) + list(merged.values()) + list(audio.values())
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
        log(f'Runtime texture observation skipped: {error}')
        return []
