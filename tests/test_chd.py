"""CHD disc images (rom/chd.py): read in place, a hunk at a time. The bit reader and Huffman tree; a whole PlayStation
2 CHD only where the developer has their own copy at hand."""

from pathlib import Path

import pytest

from decompnet.rom import chd, detect, psx

DISC = Path.home() / "Downloads" / "ps2-game.chd"


def test_bits_and_huffman():
    bits = chd._Bits(bytes([0b10110000, 0xFF]))
    assert bits.read(1) == 1 and bits.read(3) == 0b011 and bits.read(4) == 0 and bits.read(8) == 0xFF
    assert bits.read(8) == 0  # past the end: zeros


@pytest.mark.skipif(not DISC.exists(), reason="needs a PlayStation 2 CHD (the developer's own copy)")
def test_a_playstation_2_chd_reads_like_its_disc():
    with chd.Chd(DISC) as disc:
        assert psx._Disc(disc).find("SYSTEM.CNF").startswith(b"BOOT2")
    assert detect.identify(DISC).platform == "ps2"
