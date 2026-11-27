"""Observe display-list texture loads in a bounded, isolated emulator run."""
import hashlib
import tempfile
from pathlib import Path

from . import emulator, loaded_code

VIS = (1200, 2400, 4000, 5000)


def observe(image, rom, expected_sha1, *, log=lambda _: None, progress=lambda _: None, status=None):
    """Return assets backed by captured RAM and unique bytes in the analysis image."""
    from decompassets.n64 import runtime_textures

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
            snapshots = (path.read_bytes() for path in dumps.values() if path.is_file())
            assets = runtime_textures.regions(image, snapshots)
            if not assets and not run.timed_out:
                log('No textures were visible at rest; observing the title and menus with scripted input.')
                for path in dumps.values():
                    path.unlink(missing_ok=True)
                run = emulator.run(rom, VIS[-1], inputs=loaded_code.inputs(VIS[-1]), dumps=dumps,
                                   timeout=120, expansion_pak=expansion)
                snapshots = (path.read_bytes() for path in dumps.values() if path.is_file())
                assets = runtime_textures.regions(image, snapshots)
        if run.timed_out:
            log('Runtime observation timed out; any completed snapshots are still included.')
        log(f'Found {len(assets)} textures backed by runtime commands and unique cartridge bytes.')
        status['state'] = 'failed' if run.timed_out else 'complete'
        progress(dict(phase='Observing runtime textures', done=1, total=1))
        return assets
    except (OSError, emulator.EmulatorError) as error:
        log(f'Runtime texture observation skipped: {error}')
        return []
