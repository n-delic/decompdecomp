"""Run splat's config generator with a bounded nontraditional boot stub."""

import runpy

from splat.util.n64.rominfo import N64EntrypointInfo

from .splat_config import _boot_call_size

_original = N64EntrypointInfo.parse_rom_bytes


def _parse(rom_bytes, vram, offset=0x1000, size=0x60):
    return _original(rom_bytes, vram, offset, _boot_call_size(rom_bytes, vram, offset, size))


if __name__ == '__main__':
    # Scoped to this subprocess: later splat analysis reads the original image.
    N64EntrypointInfo.parse_rom_bytes = staticmethod(_parse)
    runpy.run_module('splat', run_name='__main__')
