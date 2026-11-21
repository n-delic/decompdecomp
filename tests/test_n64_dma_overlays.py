"""Synthetic raw DMA banks with independent section directory records."""

import struct

from decompnet.consoles.n64 import dma_overlays


def image_with_banks():
    size, base = 128, 0x80200000
    image = bytearray(0x300)
    for index in (1,2):
        start = 0x100+(index-1)*size
        struct.pack_into('>4s3I',image,start,b'MWo2',index,base,size)
        image[start+32:start+36] = b'Bank'
        struct.pack_into('>I',image,start+64,0x27bdffe0)
        struct.pack_into('>8I',image,32*index,base,base+size,start,start+size,
                         base+64,base+96,base+96,base+size)
    return image


def test_dma_headers_and_loader_records_agree_on_both_overlays():
    image = image_with_banks()
    result = dma_overlays.banks(image)
    assert [(bank.index,bank.start,bank.text,bank.data,bank.end) for bank in result] == [
        (1,256,320,352,384),(2,384,448,480,512),
    ]
    assert result[0].base == result[1].base == 0x80200000
    image[64+27] ^= 4
    assert not dma_overlays.banks(image)
