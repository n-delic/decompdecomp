"""Proven literal spans split text while retaining every original byte."""

import hashlib

import yaml

from decompnet.consoles.n64 import inline_data
from decompnet.rom.split import code_ranges


def test_only_an_unchanged_aligned_span_splits_executable_text(tmp_path, monkeypatch):
    context = b'precede!fakefollowing'
    signature = (context[:12].hex(), len(context), 4, hashlib.sha256(context).hexdigest())
    monkeypatch.setattr(inline_data, '_LITERALS', (signature,))
    image = b'lead' + context + bytes(8)
    config = tmp_path/'game.yaml'
    config.write_text(yaml.safe_dump(dict(segments=[
        dict(type='code', start=0, vram=0x80000000, subsegments=[[0, 'c', 'program']]),
        [len(image)],
    ])))
    assert not list(inline_data.spans(b'!' + image))
    assert not list(inline_data.spans(image[:-12] + b'?' + image[-11:]))
    inline_data.apply(config, image, lambda _: None)
    assert code_ranges(config) == [(0,12,0x80000000),(16,len(image),0x80000010)]
    segments = yaml.safe_load(config.read_text())['segments'][0]['subsegments']
    assert segments[1][:2] == [12, 'rodata']
