"""CSO disc images (also called CISO): a disc cut into blocks, each deflated on its own, what many people keep their PSP
games as (PCSX2 reads PS2 games in it too).

The header (little-endian): "CISO", its own size (0x18; 0 from older tools), the disc's size, the block size (2048),
a version and `align`; then one index entry per block and one more where the last block ends. An entry's low 31 bits
shifted left by `align` are where its block starts in the file; its high bit set means the block is stored as it is,
clear that it's raw deflate. `Cso` reads it like a file of the uncompressed disc, inflating only the blocks that are
read: the app never needs the whole disc in memory (as chd.py reads a CHD).

ZSO ("ZISO", the same layout with LZ4 blocks) and version 2 CSOs (LZ4 blocks too) need LZ4, which isn't among the
app's dependencies: they're refused with what to do instead.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

from ...rom import RomError

MAGIC, ZSO_MAGIC = b"CISO", b"ZISO"
HEADER = struct.Struct("<4sIQIBB2x")
HEADER_SIZES = (0, HEADER.size)  # a GameCube or Wii CISO has its block size here instead (2 MiB by default)
STORED = 0x80000000  # an index entry's high bit: the block isn't compressed
DEFLATE_ONLY_VERSIONS = (0, 1)  # version 2 marks LZ4 blocks with the same bit
CACHED_BLOCKS = 64


def is_cso(head: bytes) -> bool:
    """A PSP-style CSO or ZSO: the magic, then the header's own size (consoles/gc_wii/scratch.py tells a GameCube or
    Wii CISO apart the same way)."""
    return head[:4] in (MAGIC, ZSO_MAGIC) and len(head) >= 8 and struct.unpack_from("<I", head, 4)[0] in HEADER_SIZES


def _lz4_refused(what: str) -> RomError:
    return RomError(f"this is {what}, whose blocks are LZ4-compressed, which decompdecomp can't read yet: decompress "
                    "it to .iso first (maxcso does that, `maxcso --decompress`), then import the .iso")


class Cso:
    """A CSO image (a path, or its bytes), read as the disc it holds (seek/read/tell, like a file)."""

    def __init__(self, src: Path | str | bytes):
        self.f = open(src, "rb") if isinstance(src, (str, Path)) else io.BytesIO(bytes(src))
        head = self.f.read(HEADER.size)
        if len(head) < HEADER.size or not is_cso(head):
            self.f.close()
            raise RomError("this isn't a CSO image")
        magic, _, self.logical, self.block, version, self.align = HEADER.unpack(head)
        if magic == ZSO_MAGIC or version not in DEFLATE_ONLY_VERSIONS:
            self.f.close()
            raise _lz4_refused("a ZSO image" if magic == ZSO_MAGIC else f"a version {version} CSO image")
        count = -(-self.logical // self.block) + 1 if self.block else 0
        raw = self.f.read(4 * count)
        if not self.block or len(raw) != 4 * count:
            self.f.close()
            raise RomError("this CSO image is damaged (its block index is cut short): make it again from the disc")
        self.index = struct.unpack(f"<{count}I", raw)
        self.pos = 0
        self._cache: dict[int, bytes] = {}

    def _block(self, n: int) -> bytes:
        if n in self._cache:
            return self._cache[n]
        entry, after = self.index[n], self.index[n + 1]
        start, end = (entry & ~STORED) << self.align, (after & ~STORED) << self.align
        self.f.seek(start)
        raw = self.f.read(max(0, end - start))
        try:  # (a block's end may hold padding to the next aligned start: the inflater leaves it)
            data = raw if entry & STORED else zlib.decompressobj(-15).decompress(raw, self.block)
        except zlib.error:
            raise RomError(f"this CSO image is damaged (block {n} doesn't inflate): make it again from the disc") \
                from None
        data = data[:self.block].ljust(self.block, b"\0")
        if len(self._cache) >= CACHED_BLOCKS:
            self._cache.pop(next(iter(self._cache)))
        self._cache[n] = data
        return data

    # ------------------------------------------------------------------ file-like

    def seek(self, pos: int, whence: int = 0) -> int:
        self.pos = pos if whence == 0 else self.pos + pos if whence == 1 else self.logical + pos
        return self.pos

    def tell(self) -> int:
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = self.logical - self.pos
        out = bytearray()
        while n > 0 and self.pos < self.logical:
            block, at = divmod(self.pos, self.block)
            chunk = self._block(block)[at:at + min(n, self.logical - self.pos)]
            out += chunk
            self.pos += len(chunk)
            n -= len(chunk)
        return bytes(out)

    def close(self) -> None:
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
