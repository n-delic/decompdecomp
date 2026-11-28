"""Recover a bounded function decoded in place by an audited runtime loader."""

import hashlib
import struct
from pathlib import Path

from ...arch.mips_code_recovery import called_extent

# The complete loader establishes the address-derived key, byte operations,
# loop bound, cache synchronization and subsequent direct call. A digest keeps
# the game's code private and declines another loader until it is audited.
_LOADER = '57f4b6932dbbed564b93afd25be9b4490ddde45b1bccd1a6f97595ecd93fc90b'
_LOADER_SIZE = 0x13c
_MAX_SIZE = 0x10000


def find(image, ranges):
    """Return the one verified (ROM offset, size, runtime address, code size), or None."""
    from ... import consoles

    matches = set()
    for lo, hi, vram in ranges:
        if not 0 <= lo < hi <= len(image):
            continue
        at = image.find(b'\x3c\x07', lo, hi)
        while at >= 0:
            if (at % 4 == 0 and at + _LOADER_SIZE <= hi
                    and hashlib.sha256(image[at:at + _LOADER_SIZE]).hexdigest() == _LOADER):
                first, second = struct.unpack_from('>II', image, at)
                bound = struct.unpack_from('>I', image, at + 0x58)[0]
                call = struct.unpack_from('>I', image, at + 0x124)[0]
                if first >> 16 == 0x3c07 and second >> 16 == 0x24e7 and bound >> 16 == 0x2ca1 and call >> 26 == 3:
                    imm = second & 0xffff
                    address = ((first & 0xffff) << 16) + (imm - 0x10000 if imm & 0x8000 else imm)
                    size = bound & 0xffff
                    offset = lo + address - vram
                    called = ((vram + at - lo + 0x128) & 0xf0000000) | ((call & 0x3ffffff) << 2)
                    if (address == called and address % 4 == 0 and 16 <= size <= _MAX_SIZE and size % 4 == 0
                            and lo <= offset < hi and offset + size <= len(image) and offset >= at + _LOADER_SIZE
                            and not any(offset < other < offset + size for other, _, _ in ranges)):
                        decoded = transform(image[offset:offset + size], address)
                        console = consoles.get('n64')
                        extent = called_extent(decoded, 0, len(decoded), address,
                                               cpu=console.cpu_variant, asm_check=console.asm_reason)
                        if extent and extent >= 16:
                            matches.add((offset, size, address, extent))
            at = image.find(b'\x3c\x07', at + 4, hi)
    return next(iter(matches)) if len(matches) == 1 else None


def transform(data, address):
    """Apply the loader's byte arithmetic to a complete word-aligned range."""
    if len(data) % 4:
        raise ValueError('The encrypted code length must be word aligned.')
    key = sum(address.to_bytes(4, 'big')) & 0xff
    decoded = bytearray(data)
    for at in range(0, len(decoded), 4):
        decoded[at + 2] = (decoded[at + 2] - key) & 0xff
        decoded[at + 3] = (decoded[at + 3] + key) & 0xff
    return bytes(decoded)


def analysis(image, ranges):
    """Decode only the verified code, keeping every other cartridge byte."""
    if not (hit := find(image, ranges)):
        return None
    offset, size, address, _ = hit
    return image[:offset] + transform(image[offset:offset + size], address) + image[offset + size:]


def verify_analysis(original, decoded, ranges):
    """Recompute the decoded image from the original before accepting a rebuild."""
    return len(original) == len(decoded) and analysis(original, ranges) == decoded


def add(config, workdir, image, log=print):
    """Use a private decoded image when the existing code/data seam is unambiguous."""
    import yaml

    from ...rom import split

    original = (workdir / image).read_bytes()
    if not (hit := find(original, split.code_ranges(config))):
        return image
    offset, size, address, extent = hit
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    mappings = split.segments_from_config_data(cfg)
    seams = []
    for segment in cfg.get('segments', []):
        if not isinstance(segment, dict) or segment.get('type') != 'code':
            continue
        mapping = next((item for item in mappings if item.rom_start == segment.get('start')
                        and item.rom_start <= offset < offset + size <= item.rom_end
                        and item.vram + offset - item.rom_start == address), None)
        if mapping is None:
            continue
        subs = segment.get('subsegments') or []
        for i, sub in enumerate(subs):
            if not isinstance(sub, list) or len(sub) < 2 or sub[1] != 'data':
                continue
            if offset < sub[0] < offset + extent:
                following = next((item[0] for item in subs[i + 1:]
                                  if isinstance(item, list) and item and isinstance(item[0], int)), len(original))
                if following >= offset + size:
                    seams.append(sub)
    if len(seams) != 1:
        return image  # another section or mapping needs independent review
    seams[0][0] = offset + extent
    decoded = analysis(original, split.code_ranges(config))
    if decoded is None:
        return image
    name = f'{Path(image).stem}.runtime-decoded{Path(image).suffix}'
    (workdir / name).write_bytes(decoded)
    cfg.setdefault('options', {})['target_path'] = name
    cfg['sha1'] = hashlib.sha1(decoded).hexdigest()
    config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
    log('Recovered a bounded runtime-decoded function for analysis; the original cartridge is retained.')
    return name
