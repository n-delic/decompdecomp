"""Recover a program from a boot loader's chunked bitstream."""

import hashlib
import struct

from decompassets.n64.chunked_bits import decode

_LIMIT = 4 * 1024 * 1024
# The complete loader was audited against its output in independent RAM captures.
_LOADER = '9f4eee2e007ab9c5a521a86f13eb7590148c4e6607650eccf4092e0660f6ad68'
_VARIANT = '0b6a82da5b2950bfda9db4e225dc1df96cdcf63d3fe0af52386360d9385b92f5'
_VARIANT_TEXT = '31133f68985fed7e9d6871a5c31d18c1fb2c9f0d7478f21360dc527b9592e015'
_VARIANT2 = 'd23bb6845b14fa601fdfd283f00ecf4ff1674f580b4d410c6a6c998a4c7624ca'
_VARIANT3 = '163d1aa4193a5339b0e38a59a68bee64a0667206d48345662cea7fb18d3c61de'
_VARIANT3_TEXT = '1d622307d78f42131aa25aea8b8977bdaa4f382cd5853b3993936d2cc8d3c1cc'
_VARIANT2_TEXT = '536038ee4edee1e18fffba67b85e873954b3cb1adab362c169b71e3989b305ac'
_PRIMARY = '10d91d2c6916496106b399cc05b6ee0663d10a3ae9fd8003cad4936ad5476b71'
_VARIANT_PRIMARY = '3ca488c5e900ac5748c11ef50a1178305b116e6a70bf22b7826d0c39bf4683a3'
_VARIANT2_PRIMARY = '5d004f9be05eb8aa3856cedccfdf26b3bc304cae1cf736f77be6f468930ecf9b'
# Directory index, offset, output and stored sizes, runtime address, text bounds,
# and the full image digest. The text was checked against independent RAM captures.
EXTRA_PROGRAMS = (
    (1, 0x33748, 0x909b0, 0x47dd4, 0x8007a860, 0xed8, 0x7f858,
     '59f8ea8457eebd21ff8936094b0ee5fb875158644e46652e6da8ba30d2249b0f'),
    (2, 0x7b51c, 0x90810, 0x49cb8, 0x8007a860, 0x101f0, 0x82c2c,
     'aa61544efd5d4fdead01fe0064c4be710d3675a763e7ec86b2702a4795a8d785'),
    (4, 0xc51d8, 0x33310, 0x1a9b0, 0x8010b210, 0x8, 0x33310,
     '379e415c25f7841e3053233aecc6afc146dee3ec62f31a32502df74f97decc3d'),
    (5, 0xdfb88, 0x337e0, 0x1a6c8, 0x8010b210, 0x1048, 0x2be04,
     '96d59f2c1077f726937795b45ac0a7fa10fa1d5a0942c5fb589ac7dd592eb451'),
)
VARIANT_PROGRAMS = (
    (1, 0x39534, 0x40570, 0x19010, 0x800838b8, 0x1020, 0x2d074,
     'f6ff153e08417fd99801d0889c5448a064e47b09d4bb923c52177c928e265905'),
    (2, 0x52544, 0x2ba90, 0x17318, 0x800c3e28, 0x8, 0x2ba90,
     'ed9dc915524e4db9f21b3f62d575cf096cea1cff36379187ec431a2ae5929f6a'),
    (3, 0x6985c, 0xa6d8, 0x5f3c, 0x800eff78, 0x8, 0xa6d4,
     '4a4d49481261db3dc6687fdc7a78e4400735f92f6e41f9f84b726573c441b5f0'),
    (4, 0x6f798, 0x3c290, 0x20970, 0x800fbb18, 0x40, 0x3c1f4,
     'b8bcfca252b273b2753c598f9c9f7d47b7d8920f5a38c0f70e1fc7b59c827813'),
    (6, 0x959d8, 0x9200, 0x5724, 0x80137da8, 0x168, 0x8f8c,
     '85e5428d76c7f07b676a697e16ec340b32800983b13e25dbb7cc491e94ee569a'),
    (7, 0x9b0fc, 0x3f340, 0x20e50, 0x800838b8, 0x18b8, 0x3b7dc,
     '108e730f8aa0a1326952f45c1d82d6250c2d27b6c639d82bf6908ac01e3a47ca'),
    (8, 0xbbf4c, 0x2c150, 0x1712c, 0x800c3e28, 0x1190, 0x292bc,
     '3bb1c933a356e25e7513e6a710a1eaeff5d8d10d5b153c75c46be7bbc716d670'),
    (9, 0xd3078, 0x3bfd0, 0x1f194, 0x800fbb18, 0xe38, 0x388f0,
     '7d307e9a46cf04434436248ee57889518cf3f4f0e54cb23d365b2fc34566396c'),
    (10, 0xf220c, 0x8288, 0x4818, 0x80137da8, 0x8, 0x8288,
     'ca2978240f8f0180aabaa69318ad3ccec24ae10d01b41fe1684e298820b286a5'),
)
VARIANT2_PROGRAMS = (
    (1, 0x39be8, 0x40598, 0x1b0d8, 0x800838d0, 0x1020, 0x2d06c,
     '424cd9b3dd8d8d869fe0a7970a5f59bb20b6fe5e1453edfeb97b11c63fc04150'),
    (2, 0x54cc0, 0x29f48, 0x1850c, 0x800c3e68, 0x8, 0x29f3c,
     'ac5ea066b62ec51cede96ca987d01a810f504fc13582f77afc9c41574e33eede'),
    (3, 0x6d1cc, 0xa2c0, 0x6508, 0x800efbf8, 0x8, 0xa2bc,
     '65d6ef921bbf57fe168307542ce67281cbc7fc40ace2f494a10c25df2822eccb'),
    (4, 0x736d4, 0x3f388, 0x24c0c, 0x800fb428, 0x58, 0x3f2f0,
     'c83acbf330f3cf7f70830a699217fdcaf847b8a83c3d59acd61ccf29c8ebab4b'),
    (6, 0x9e1f4, 0x9290, 0x5c08, 0x8013a7b0, 0x168, 0x901c,
     '7ae10a6b88c58abffdc8b8d5e1bb35bd0e355a7784200e43074343c9d44f9890'),
    (7, 0xa3dfc, 0x3e500, 0x22134, 0x800838d0, 0x1920, 0x3a7ac,
     'b2b95c99f54ae92fc03d682454d252bc0f7c6fe167386edc7a0d55b241999f44'),
    (8, 0xc5f30, 0x2bd90, 0x18254, 0x800c3e68, 0x11e8, 0x28e74,
     'c8a3d3757177934311ff42679b9f84447b450aea02e61537826a966728f972ee'),
    (9, 0xde184, 0x3a678, 0x203f4, 0x800fb428, 0xe38, 0x36f7c,
     '5e784f12647cf836ea5fd55d8f336417ffb5cd5a5f25c9d9bf8c69b374b74332'),
    (10, 0xfe578, 0x83a8, 0x4a58, 0x8013a7b0, 0x8, 0x8394,
     'b1840371b0cfca33625faf522ec386f841ed6ef65ad8ce72746c4a58dc4c873e'),
)


def drop_impossible_bss(segments):
    """Discard a mistaken size larger than all N64 RDRAM, keeping valid sizes."""
    for segment in segments:
        if (isinstance(segment, dict) and segment.get('type') == 'code'
                and isinstance(segment.get('bss_size'), int) and segment['bss_size'] > 0x800000):
            segment.pop('bss_size')


def code_start(vram, text):
    if vram == 0x80000400 and hashlib.sha256(text).hexdigest() == _VARIANT3_TEXT:
        return 72
    if vram == 0x80000400:
        return 96 if len(text) >= 96 and not any(text[80:96]) else 80
    return next((start for _, _, _, _, address, start, end, _ in EXTRA_PROGRAMS + VARIANT_PROGRAMS + VARIANT2_PROGRAMS
                 if address == vram and end == len(text)), None)



def variant_sections(text):
    """An audited program puts startup code before a large data table."""
    digest = hashlib.sha256(text).hexdigest()
    if digest == _VARIANT_TEXT:
        return 96, 0xa8, 0x5748
    if digest == _VARIANT2_TEXT:
        return 112, 0xb8, 0x5aa0
    return None


def repair_variant_entry(segments, rom):
    """Keep an audited boot helper whole when the initial split cuts it in two."""
    if hashlib.sha256(rom[0x1000:0x1b00]).hexdigest() not in (_VARIANT, _VARIANT2):
        return False
    entry = next((s for s in segments if isinstance(s, dict) and s.get('name') == 'entry'), None)
    main = next((s for s in segments if isinstance(s, dict) and s.get('name') == 'main'), None)
    if (not entry or not main or entry.get('start') != 0x1000 or entry.get('vram') != 0x80200400
            or main.get('start') != 0x105c or main.get('vram') != 0x8020045c
            or main.get('subsegments') != [[0x105c, 'c', 'main']]):
        return False
    main['start'], main['vram'] = 0x10fc, 0x802004fc
    main['subsegments'][0][0] = 0x10fc
    # The audited loader uses this stack-frame size as an immediate; it is
    # not a BSS extent. Keeping the inferred value invents a relocation.
    if main.get('bss_size') == 0x38:
        main.pop('bss_size')
    return True


def repair_variant_relocations(path, rom):
    """Remove the two false stack-frame relocations emitted for this loader."""
    if hashlib.sha256(rom[0x1000:0x1b00]).hexdigest() not in (_VARIANT, _VARIANT2) or not path.is_file():
        return False
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    false = {'rom:0x001030 reloc:MIPS_HI16 symbol:main_BSS_SIZE',
             'rom:0x001038 reloc:MIPS_LO16 symbol:main_BSS_SIZE'}
    if not false.issubset({line.strip() for line in lines}):
        return False
    path.write_text(''.join(line for line in lines if line.strip() not in false), encoding='utf-8')
    return True

def find(rom, boot_ranges):
    """Require the audited loader, its directory and a closed decoded program."""
    from .code import _looks_like_code

    # Splat may split this contiguous mapping at the entrypoint's call.
    covered = sorted((lo, hi) for lo, hi, base in boot_ranges if base - lo == 0x801ff400)
    cursor = 0x1000
    for lo, hi in covered:
        if lo <= cursor < hi:
            cursor = hi
    digest = hashlib.sha256(rom[0x1000:0x1b00]).hexdigest() if cursor >= 0x1b00 else None
    if digest == _VARIANT:
        directory, startup = 0x1b68, 96
    elif digest == _VARIANT2:
        directory, startup = 0x1b70, 112
    elif (cursor >= 0x16e0 and len(rom) >= 0x16ec
          and hashlib.sha256(rom[0x1000:0x16e0]).hexdigest() == _LOADER):
        pointer = struct.unpack_from('>I', rom, 0x16e8)[0]
        if not 0xb0000000 <= pointer < 0xb0000000 + len(rom):
            return None
        directory, startup = pointer - 0xb0000000, 80
    elif (cursor >= 0x16e0 and len(rom) >= 0x1748
          and hashlib.sha256(rom[0x1000:0x1738]).hexdigest() == _VARIANT3):
        directory, startup = 0x1738, 72
    else:
        return None
    if directory % 4 or directory + 16 > len(rom):
        return None
    first, expanded, encoded, following = struct.unpack_from('>4I', rom, directory)
    packed = encoded & 0x7fffffff
    if (not encoded & 0x80000000 or not 16 <= first <= 4096 or first % 12
            or not 0x1000 <= expanded <= _LIMIT or not 0 < packed <= _LIMIT
            or following != (first + packed + 1) & ~1 or directory + following > len(rom)):
        return None
    data = decode(rom[directory + first:directory + first + packed], expanded)
    if not data or any(data[:startup]) or not _looks_like_code(data[startup:]):
        return None
    returns = [i for i in range(startup, len(data) - 7, 4) if data[i:i + 4] == b'\x03\xe0\x00\x08']
    if len(returns) < 16:
        return None
    end = returns[-1] + 8
    primary = (0x80000400, data[:end], data[end:])
    digest = hashlib.sha256(data).hexdigest()
    extras = { _PRIMARY: EXTRA_PROGRAMS, _VARIANT_PRIMARY: VARIANT_PROGRAMS,
              _VARIANT2_PRIMARY: VARIANT2_PROGRAMS }.get(digest, ())
    if not extras:
        return (primary,)
    programs = [primary]
    for index, offset, expanded, packed, vram, start, end, digest in extras:
        if directory + 12 * (index + 1) > len(rom):
            return (primary,)
        if struct.unpack_from('>III', rom, directory + 12 * index) != (offset, expanded, 0x80000000 | packed):
            return (primary,)
        source = directory + offset
        if source + packed > len(rom):
            return (primary,)
        decoded = decode(rom[source:source + packed], expanded)
        if (decoded is None or not 0 <= start < end <= len(decoded)
                or hashlib.sha256(decoded).hexdigest() != digest):
            return (primary,)
        programs.append((vram, decoded[:end], decoded[end:]))
    return tuple(programs)
