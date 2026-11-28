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


def test_paired_legacy_mesh_reaches_console_preview():
    import struct

    from decompassets.n64 import paired_resource_models
    from decompnet.consoles import get

    vertices = b''.join(struct.pack('>3hH2h4B', x, y, 0, 0, 0, 0, 255, 255, 255, 255)
                        for x, y in ((0, 0), (8, 0), (0, 8)))
    mesh = struct.pack('>6I', 0x04000c2f, 0x06000000, 0xbf000000, 0x00000204, 0xb8000000, 0)
    data = vertices + mesh
    chunks = [data[i:i+31] for i in range(0, len(data), 31)]
    payload = b''.join(bytes([0x80|len(chunk)])+chunk for chunk in chunks)
    image = struct.pack('>I', len(payload)+4)+payload
    entries = paired_resource_models.meshes(data)
    asset = paired_resource_models.asset(0, len(image), None, entries)
    result = get('n64').asset_preview(image, asset, {})
    assert result['triangles'] == 1
    assert result['surfaces'][0]['indices'] == (0, 1, 2)


def test_paired_mesh_preview_uses_its_corroborated_segment():
    import struct

    from decompassets.n64 import paired_resource_models
    from decompnet.consoles import get

    vertices = b''.join(struct.pack('>3hH2h4B', x, y, 0, 0, 0, 0, 255, 255, 255, 255)
                        for x, y in ((0, 0), (8, 0), (0, 8)))
    mesh = struct.pack('>6I', 0x01003006, 0x03000000, 0x05000204, 0, 0xdf000000, 0)
    data = vertices + mesh
    chunks = [data[i:i+31] for i in range(0, len(data), 31)]
    payload = b''.join(bytes([0x80|len(chunk)])+chunk for chunk in chunks)
    image = struct.pack('>I', len(payload)+4)+payload
    entry = dict(label='Mesh',model_offset=len(vertices),model_size=len(mesh),segment=3)
    asset = paired_resource_models.asset(0, len(image), None, [entry])
    result = get('n64').asset_preview(image, asset, {})
    assert result['triangles'] == 1
    assert result['surfaces'][0]['indices'] == (0, 1, 2)


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


def test_chunked_indexed_image_reaches_console_preview():
    import struct

    from decompnet.consoles import get

    decoded = (struct.pack(">4HI2H", 8, 1, 1, 0xffff, 72, 8, 8) + bytes(range(64))
               + bytes.fromhex("020601000008") + bytes(512))
    chunks = [decoded[i:i+127] for i in range(0, len(decoded), 127)]
    packed = b"12345" + b"".join(bytes([0x80 | len(chunk)]) + chunk for chunk in chunks) + b"\0"
    asset = dict(layout="chunked-lz-indexed-image", offset=0, size=len(packed),
                 expanded=len(decoded), reason="Validated directory and embedded palette.")
    result = get("n64").asset_preview(packed, asset, {})
    assert result["media"] == "image"
    assert (result["details"]["width"], result["details"]["height"]) == (8, 8)


def test_chunked_runtime_mesh_reaches_console_preview():
    import struct

    from decompnet.consoles import get

    data = bytearray(0x200)
    for i, point in enumerate(((0, 0, 0), (100, 0, 0), (0, 100, 0), (0, 0, 100))):
        struct.pack_into(">3hH2h4B", data, 0x100+i*16, *point, 0, i, i, 20+i, 40+i, 60+i, 255)
    for i, command in enumerate(((0x0400103f, 0x01000000), (0xb1000204, 0x00000406),
                                 (0xb1000602, 0x00020604))):
        struct.pack_into(">II", data, 0x20+i*8, *command)
    chunks = [data[i:i+127] for i in range(0, len(data), 127)]
    packed = b"12345" + b"".join(bytes([0x80 | len(chunk)]) + chunk for chunk in chunks) + b"\0"
    asset = dict(layout="runtime-chunked-model", offset=0, size=len(packed), expanded=len(data),
                 label="Local mesh", meshes=[dict(command=0x20, command_size=24, vertex=0x100)])
    result = get("n64").asset_preview(packed, asset, {})
    assert result["media"] == "model" and result["triangles"] == 4


def test_ea_xa_sound_bank_reaches_console_preview():
    import struct

    from decompnet.consoles import get

    image = bytearray(0x40 + 15)
    image[:20] = b"BNKb\x04\0\0\x01\0\0\0\x40\0\0\0\x0f\0\0\0\0"
    image[20:24] = struct.pack(">I", 4)
    record = (b"PT\x02\0\xfd\x85\x02\0\x1c\x84\x02\x3e\x80"
              b"\x83\x01\x07\x88\x04\0\0\0\x40\xff")
    image[24:24+len(record)] = record
    image[0x40:] = b"\x0c" + b"\x1f"*14
    from decompassets.n64 import bnkb_audio
    bank, = bnkb_audio.regions(image)
    result = get("n64").asset_preview(image, bank, {})
    assert result["media"] == "audio" and result["details"]["original_samples"] == 28
