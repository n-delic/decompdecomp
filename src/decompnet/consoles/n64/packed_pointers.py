"""Cartridge words that resemble MIPS addresses but belong to graphics data."""

import struct

from decompassets import formats
from decompassets.n64.display_lists import _vertex_count


def audio_payloads(image, segments):
    """Only encoded samples bounded by both a BOLT audio member and a stream chain."""
    from decompassets.n64 import bolt, stream_ima

    members = [m for _, rows in bolt.archives(image, lambda: None) for m in rows
               if m.kind == 25 and m.stored]
    if not members:
        return ()
    ranges = []
    for bank in stream_ima.regions(image):
        for sample in bank['samples']:
            lo, hi = sample['offset'], sample['offset']+sample['size']
            if (any(m.offset <= lo and hi <= m.offset+m.expanded for m in members)
                    and not any(s.rom_start < hi and lo < s.rom_end for s in segments)):
                ranges.append((lo,hi))
    return ranges


def is_data(image, offset):
    # A G_VTX address followed by the rest of its vertex batch and a triangle.
    if offset >= 12 and offset % 8 == 4 and offset+28 <= len(image):
        words = list(struct.iter_unpack('>II', image[offset-12:offset+28]))
        vertex = [(word, arg) for word,arg in words[:3]
                  if word>>24 == 1 and _vertex_count(word) and 0x80000000 <= arg < 0x80800000
                  and arg%8 == 0]
        if (len(vertex)==3 and len({(word>>1)&127 for word,_ in vertex})==3
                and any(word>>24 == 5 and arg==0 for word,arg in words[3:])):
            return True

    # In a checked Yay0 stream the mask table's words select tokens, even
    # when their bit pattern equals an address in a completely different bank.
    start = image.rfind(b'Yay0', max(0,offset-0x2000), offset)
    if start < 0 or start%4 or offset < start+16 or start+16 > len(image):
        return False
    size, links, chunks = struct.unpack_from('>3I',image,start+4)
    if not (0 < size <= 0x20000 and 16 <= offset-start <= links-4
            and links%4 == 0 and links <= chunks < len(image)-start):
        return False
    try:
        decoded, _ = formats.unpack(image[start:min(len(image),start+chunks+size)])
    except formats.AssetError:
        return False
    return len(decoded)==size
