"""Relocated SDK identities use the loader's relocation layout and the full known function."""
import hashlib
from types import SimpleNamespace

from decompnet.consoles.n64 import port_relocated, uso
from decompnet.elfobj import Reloc, mask_relocs


def test_only_unique_hosted_sdk_fingerprints_are_used(tmp_path, monkeypatch):
    # A homemade 32-byte function with one relocated jump: the SDK table records masked bytes and layout.
    code = bytes.fromhex('27bdffe0afbf001c0c000000000000008fbf001c27bd002003e0000800000000')
    layout = [(8, 4)]
    masked = mask_relocs(code, [Reloc(8, 4, '', 0)], 'mips', True)
    digest = hashlib.sha1(masked)
    digest.update(repr(layout).encode())
    table = tmp_path / 'ultralib_signatures.tsv'
    table.write_text(f'{digest.hexdigest()}\t32\tosTest\tunit\n')
    region = SimpleNamespace(data=code, entries=(0,), relocations=((8, 4, 1),))
    monkeypatch.setattr(uso, 'read', lambda image: [region])
    monkeypatch.setattr(port_relocated, '_table', lambda: table)
    results = port_relocated.patterns(b'image', {'osTest'})
    assert len(results) == 1 and results[0][0] == 'osTest'
    _, words, masks = results[0]
    assert words[2] == 0x0C000000 and masks[2] == 0xFC000000
    assert not port_relocated.patterns(b'image', set())
    table.write_text(table.read_text() + f'{digest.hexdigest()}\t32\tosOther\tunit\n')
    assert not port_relocated.patterns(b'image', {'osTest', 'osOther'})


def test_generated_pattern_declares_host_even_without_a_fixed_address(tmp_path):
    from decompnet.port.wasm import interp

    host = tmp_path / 'host.c'
    host.write_text('u32 w2c_env_osTest(struct w2c_env *e, u32 a) { return a; }\n')
    text, _ = interp.generate('', [], [host], patterns=[('osTest', (0x12345678,), (0xFFFFFFFF,))])
    assert 'u32 w2c_env_osTest(struct w2c_env *, u32);' in text
    assert 'w2c_env_osTest(NULL, W(r, 0))' in text
    assert 'const unsigned port_mips_pattern_count = 1;' in text
