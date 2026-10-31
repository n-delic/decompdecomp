"""Xbox disc images: XISO and full Redump layouts are recognised and files read out of them (consoles/xbox/xdvdfs.py)."""

import struct

import pytest

from decompnet.consoles.xbox import xdvdfs
from decompnet.forks.project import ForkError

S = xdvdfs.SECTOR


def entry(name, sector, size, attrs, right=0):
    raw = struct.pack("<HHIIBB", 0, right, sector, size, attrs, len(name)) + name.encode()
    return raw + b"\xff" * (-len(raw) % 4)


def xiso() -> bytes:
    """default.xbe in the root, media/intro.xmv in a subdirectory; the filesystem starts at byte 0."""
    root = entry("default.xbe", 34, 5, 0x80, right=0)
    sub = entry("media", 35, S, 0x10)
    # a tree: "media" first, its right child (offset in 4-byte units) is default.xbe
    media = entry("media", 35, S, 0x10, right=len(sub) // 4)
    root = media + root
    inner = entry("Intro.XMV", 36, 3, 0x80)
    img = bytearray(37 * S)
    img[32 * S:32 * S + 20] = xdvdfs.MAGIC
    struct.pack_into("<II", img, 32 * S + 0x14, 33, len(root))
    img[33 * S:33 * S + len(root)] = root
    img[34 * S:34 * S + 5] = b"XBEH!"
    img[35 * S:35 * S + len(inner)] = inner
    img[36 * S:36 * S + 3] = b"xmv"
    return bytes(img)


@pytest.fixture
def images(tmp_path):
    iso = tmp_path / "a.iso"
    iso.write_bytes(xiso())
    dump = tmp_path / "b.iso"
    with open(dump, "wb") as f:
        f.seek(xdvdfs.LAYOUTS["redump"])  # sparse: the video partition stays unwritten
        f.write(xiso())
    other = tmp_path / "c.bin"
    other.write_bytes(b"\0" * 0x20000)
    return iso, dump, other


def test_recognised(images):
    iso, dump, other = images
    assert xdvdfs.is_xbox_disc(iso) and xdvdfs.is_xbox_disc(dump)
    assert not xdvdfs.is_xbox_disc(other)
    assert xdvdfs.is_xbox_disc(xiso())


def test_read_file(images):
    for disc in images[:2]:
        assert xdvdfs.read_file(disc, "default.xbe") == b"XBEH!"
        assert xdvdfs.read_file(disc, "DEFAULT.XBE") == b"XBEH!"
        assert xdvdfs.read_file(disc, "Media/intro.xmv") == b"xmv"


def test_missing_file(images):
    with pytest.raises(ForkError, match="default.xbe"):
        xdvdfs.read_file(images[0], "nothere.xbe")
    with pytest.raises(ForkError, match="Xbox disc"):
        xdvdfs.read_file(images[2], "default.xbe")
