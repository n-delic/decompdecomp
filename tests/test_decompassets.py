"""decompassets is a dependency (RetroReversing/decomp-assets, which tests the library itself); this checks how the
app uses it."""
import importlib

N64 = ("textures", "audio", "ptr_audio", "models", "model_links", "model_scenery", "archived_models", "texture_packs",
       "animation", "fonts", "resources", "resource_inspection", "native_scene")


def test_app_names_are_the_library_modules():
    """The app keeps its old names for the asset readers; both names must be one module (patches reach both)."""
    from decompassets import archives, formats, rnc
    from decompnet.consoles.n64 import rnc as n64_rnc
    from decompnet.rom import asset_archives, asset_formats
    assert (asset_formats, asset_archives, n64_rnc) == (formats, archives, rnc)
    for name in N64:
        assert importlib.import_module(f"decompnet.consoles.n64.{name}") is importlib.import_module(f"decompassets.n64.{name}")


def test_one_asset_error_for_every_caller():
    """ROM Explorer catches AssetError from rom.asset_formats; the library's recognisers raise the same class."""
    from decompassets.formats import AssetError
    from decompnet.rom.asset_formats import AssetError as AppAssetError
    assert AssetError is AppAssetError


def test_constructed_packed_texture_reaches_console_preview():
    import struct

    from decompnet.consoles import get

    pixels = bytes([0x01])*120
    chunks = [pixels[i:i+31] for i in range(0, len(pixels), 31)]
    packed = b''.join(bytes([0x80|len(chunk)])+chunk for chunk in chunks)
    packed = struct.pack('>I', len(packed)+4)+packed
    image = packed + bytes.fromhex('f80107c1') + bytes(28)
    asset = dict(layout='constructed-resource-texture', offset=0, size=len(packed),
                 width=16, height=15, palette_offset=len(packed), reason='Tested loader and drawing code.')
    result = get('n64').asset_preview(image, asset, {})
    assert result['media'] == 'image'
    assert (result['details']['width'], result['details']['height']) == (16, 15)


def test_the_n64_console_gives_the_library_its_mips_reader(monkeypatch):
    """Audio banks find their sample tables from loader calls, which only the app can read."""
    from decompassets import n64
    from decompnet.arch import mips_assets
    from decompnet.consoles import get
    seen = {}
    monkeypatch.setattr(n64, "regions", lambda image, *, context=None, **kwargs: seen.update(context) or [])
    get("n64").asset_regions(b"", context={"gp": 4})
    assert seen == {"read_code": mips_assets.read, "gp": 4}


def test_the_library_never_imports_the_app():
    from pathlib import Path

    import decompassets
    for path in Path(decompassets.__file__).parent.rglob("*.py"):
        assert "decompnet" not in path.read_text(), path



def test_chunked_package_image_reaches_the_console_preview():
    import struct

    from decompnet.consoles import get

    data = (b'PKG\0' + struct.pack('<I', 0x210) + struct.pack('>II4BI', 1, 24, 1, 1, 1, 0, 16)
            + bytes(8) + b'\xff\xff' * 256)
    stream = b''.join(b'\x00' + data[i:i+8] for i in range(0, len(data), 8)) + b'\x03\x00\x00'
    image = bytes(16) + stream + bytes(-len(stream) % 4)
    console = get('n64')
    assets = [asset for asset in console.asset_regions(image) if asset.get('layout') == 'chunked-package-image']
    assert len(assets) == 1
    preview = console.asset_preview(image, assets[0], {})
    assert preview['media'] == 'image' and preview['details']['width'] == 8
    assert preview['data_url'].startswith('data:image/png;base64,')


def test_paged_texture_and_model_reach_console_previews():
    import struct

    from decompnet.consoles import get

    page = bytearray(8192)
    mesh = bytearray(124)
    struct.pack_into('>2I', mesh, 0, 1, 96)
    for i, point in enumerate(((0, 0, 0), (10, 0, 0), (0, 20, 0))):
        struct.pack_into('>3hHhhI', mesh, 48+i*16, *point, 0, 0, 0, 0xffffffff)
    struct.pack_into('>4I', mesh, 96, 3, 48, 3, 112)
    mesh[112:124] = bytes.fromhex('100300002001000030000102')
    page[100:108] = b'ARN\0' + struct.pack('<I', len(mesh))
    page[108:108+len(mesh)] = mesh
    texture = bytearray(536)
    struct.pack_into('>2I4BI', texture, 0, 1, 24, 1, 1, 1, 0, 16)
    texture[24:] = b'\xff\xff' * 256
    page[500:508] = b'PKG\0' + struct.pack('<I', len(texture))
    page[508:508+len(texture)] = texture
    image = b'testBIG\0' + struct.pack('>I2I', len(page), 20 << 4, (20 + len(page)) << 4) + page
    console = get('n64')
    assets = console.asset_regions(image)
    model = next(item for item in assets if item.get('layout') == 'big-resource-model')
    picture = next(item for item in assets if item.get('layout') == 'big-resource-images')
    assert console.asset_preview(image, model, {})['triangles'] == 1
    assert console.asset_preview(image, picture, {})['details']['width'] == 8


def test_paged_audio_reaches_console_previews():
    import struct

    from decompnet.consoles import get

    page = bytearray(8192)
    page[100:108] = b'idx\0' + struct.pack('<I', 44)
    struct.pack_into('>5I', page, 108, 2, 0, 0, 68, 0)
    struct.pack_into('>5I', page, 128, 0x12345678, 1, 68, 68, 1)
    page[300:308] = b'SAM\0' + struct.pack('<I', 36)
    struct.pack_into('>8I', page, 308, 5, 2, 0, 0, 16000, 0x80000000, 60, 32)
    page[340:344] = bytes.fromhex('52001f00')
    archive_size = 20 + len(page)
    archive = b'testBIG\0' + struct.pack('>I2I', len(page), 20 << 4, archive_size << 4) + page
    big_row = b'sfx.big\0'.ljust(16, b'\0') + struct.pack('>4I', 0, archive_size, 0, 0)
    raw_row = b'big.raw\0'.ljust(16, b'\0') + struct.pack('>4I', 0, 136, archive_size, 0)
    block = struct.pack('>hhB', 1, 100, 0) + b'\x1f' + bytes(62)
    image = big_row + raw_row + archive + block * 2
    console = get('n64')
    assets = console.asset_regions(image)
    for layout in ('big-indexed-audio', 'big-sam-audio'):
        bank = next(item for item in assets if item.get('layout') == layout)
        assert console.asset_preview(image, bank, {})['mime'] == 'audio/wav'
