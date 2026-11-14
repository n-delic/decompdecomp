"""Images a forked project's own splat config names, as ROM Explorer assets.

A decompilation that has worked out where its textures are writes them into its splat config: a segment of an
image type (`[0x1234, rgba16, name, 32, 32]`, `ci4` and `ci8` with a `palette` segment of the same name or listed
in `palettes`), or a project's own segment type whose arguments are an image format and its size
(`[0x1BB280, yay0_image, Font/000, i4, 24, 24]`), where the bytes are often packed (Yay0, MIO0, Yaz0, zlib).
ROM Explorer shows them as the project declares them: their offsets are the project's, not found by a recogniser,
so they are read only from a config that splits exactly the game file the project asks for
(`ForkProject.splat_config_for`).
"""
from __future__ import annotations

from . import asset_formats
from .asset_formats import AssetError

# splat's image types: the preview format, bits per texel and the name shown.
FORMATS = {'rgba16': ('rgba5551-be', 16, 'RGBA16'), 'rgba32': ('rgba8888', 32, 'RGBA32'), 'ci4': ('ci4', 4, 'CI4'),
           'ci8': ('ci8', 8, 'CI8'), 'i4': ('i4', 4, 'I4'), 'i8': ('i8', 8, 'I8'), 'ia4': ('ia4', 4, 'IA4'),
           'ia8': ('ia8', 8, 'IA8'), 'ia16': ('ia16', 16, 'IA16')}
PALETTE_ENTRIES = {'ci4': 16, 'ci8': 256}
PACKED = (b'Yay0', b'Yaz0', b'MIO0')
MAX_IMAGES = 20000


def _entries(segments, end=None):
    """Each segment in a list, flattened through `subsegments`: (start, end, type, name, arguments, item)."""
    items = []
    for item in segments if isinstance(segments, list) else []:
        if isinstance(item, list) and item and isinstance(item[0], int):
            items.append((item[0], item[1] if len(item) > 1 else None, item[2] if len(item) > 2 else None, item[3:], item))
        elif isinstance(item, dict) and isinstance(item.get('start'), int):
            items.append((item['start'], item.get('type'), item.get('name'), [], item))
    out = []
    for index, (start, kind, name, args, item) in enumerate(items):
        stop = next((s for s, *_ in items[index+1:] if s > start), end)
        out.append((start, stop, kind, name, args, item))
        if isinstance(item, dict) and 'subsegments' in item:
            out += _entries(item['subsegments'], stop)
    return out


def _image(kind, name, args, item):
    """(format, width, height) a segment declares, or None."""
    if kind in FORMATS:
        width, height = (args + [None, None])[:2] if isinstance(item, list) else (item.get('width'), item.get('height'))
        return kind, width, height
    if isinstance(item, list) and len(args) >= 3 and args[0] in FORMATS:
        return args[0], args[1], args[2]
    return None


def images(config):
    """The images a splat config declares: offset, end, format, width, height, name and palette offset."""
    entries = _entries(config.get('segments') if isinstance(config, dict) else None)
    palettes = {}
    for start, _stop, kind, name, _args, _item in entries:
        if kind == 'palette' and isinstance(name, str):palettes.setdefault(name, start)
    out = []
    for start, stop, kind, name, args, item in entries:
        found = _image(kind, name, args, item)
        if not found:continue
        fmt, width, height = found
        if not all(isinstance(v, int) and 0 < v <= asset_formats.MAX_DIMENSION for v in (width, height)):continue
        if not isinstance(name, str) or not name:name = f'{start:X}'
        palette = None
        if kind in PALETTE_ENTRIES or fmt in PALETTE_ENTRIES:
            named = item.get('palettes') if isinstance(item, dict) else None
            named = named[0] if isinstance(named, list) and named else named
            palette = palettes.get(named if isinstance(named, str) else name)
            if palette is None:continue
        preview, bits, shown = FORMATS[fmt]
        out.append(dict(offset=start, end=stop, format=preview, bits=bits, shown=shown, width=width, height=height,
                        name=name, palette=palette, declared=kind))
        if len(out) >= MAX_IMAGES:break
    return out


def _bytes(image, offset, size):
    """`size` bytes at `offset`, unpacked first when a packed block starts there: (bytes, codec or None)."""
    head = bytes(image[offset:offset+4])
    if head in PACKED:
        raw, codec = asset_formats.unpack(bytes(image[offset:offset+asset_formats.MAX_BYTES]))
        return raw[:size], codec
    return bytes(image[offset:offset+size]), None


def _pixels(item):
    return (item['width']*item['bits'] + 7)//8*item['height']


def regions(image, declared, source):
    """ROM Explorer assets for the declared images whose bytes are there (raw, or a packed block that unpacks)."""
    out = []
    for item in declared:
        size = _pixels(item)
        packed = bytes(image[item['offset']:item['offset']+4]) in PACKED
        if not packed and item['offset'] + size > len(image):continue
        span = (item['end'] - item['offset']) if item['end'] and item['end'] > item['offset'] else size
        settings = dict(format=item['format'], width=item['width'], height=item['height'], offset=item['offset'], source='rom')
        if item['palette'] is not None:settings.update(palette_offset=item['palette'], palette_format='rgba5551-be')
        out.append(dict(offset=item['offset'], size=min(span, len(image) - item['offset']), kind='image', provider='splat-config',
                        label=f"{item['name']} · {item['shown']} {item['width']}×{item['height']}", confidence='declared',
                        resource_path=item['name'], declared=item, packed=packed, preview_settings=settings, frames=1,
                        palettes=1 if item['palette'] is not None else 0, frame_offsets=[item['offset']],
                        palette_offsets=[item['palette']] if item['palette'] is not None else [],
                        palette_entries=[PALETTE_ENTRIES[item['format']]] if item['palette'] is not None else [],
                        levels=[dict(width=item['width'], height=item['height'], offset=0)],
                        reason=f"The project's splat config ({source}) declares a {item['declared']} segment here: "
                               f"{item['shown']} {item['width']}×{item['height']}"
                               + (', packed' if packed else '') + '. The project worked out this layout; it is shown as declared.'))
    return out


def preview(image, asset, selection):
    """The declared image, unpacked first when its bytes are a packed block, with its palette."""
    item = asset['declared']
    texels, codec = _bytes(image, item['offset'], _pixels(item))
    if len(texels) < _pixels(item):raise AssetError('The declared image is shorter than its size. Check the project\'s splat config, then rescan.')
    settings = dict(format=item['format'], width=item['width'], height=item['height'], offset=0)
    data = texels
    if item['palette'] is not None:
        palette, _ = _bytes(image, item['palette'], 2*PALETTE_ENTRIES[item['format']])
        settings.update(palette_offset=len(texels), palette_format='rgba5551-be')
        data = texels + palette
    result = asset_formats.preview(data, settings)
    result['details'].update(texture=0, frame=0, palette=0 if item['palette'] is not None else 'none', note=asset['reason'])
    if codec:result['decoded'] = dict(codec=codec, bytes=len(texels), block_offset=item['offset'], member=item['name'])
    result['settings'] = dict(source='rom', format='auto', offset=item['offset'], texture=0, frame=0, palette=0, mip=0)
    return result
