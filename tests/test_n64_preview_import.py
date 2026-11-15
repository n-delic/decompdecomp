"""Concurrent ROM previews must not read a half-initialized compatibility alias."""
import sys
from types import ModuleType

import pytest

from decompassets.formats import AssetError
from decompassets.n64 import textures
from decompnet.consoles import get


@pytest.mark.parametrize('result', [None, (b'synthetic decoded bytes', 'synthetic codec')])
def test_preview_decoder_uses_library_during_alias_import(monkeypatch, result):
    monkeypatch.setitem(sys.modules, 'decompnet.consoles.n64.textures',
                        ModuleType('decompnet.consoles.n64.textures'))
    seen = []
    def decode(data):
        seen.append(data)
        return result
    monkeypatch.setattr(textures, 'decode', decode)
    assert get('n64').asset_decode(b'synthetic input') == result
    assert seen == [b'synthetic input']


def test_preview_decoder_keeps_actionable_asset_error(monkeypatch):
    monkeypatch.setitem(sys.modules, 'decompnet.consoles.n64.textures',
                        ModuleType('decompnet.consoles.n64.textures'))
    error = AssetError('Synthetic incomplete block')
    def decode(data):raise error
    monkeypatch.setattr(textures, 'decode', decode)
    with pytest.raises(AssetError) as failure:get('n64').asset_decode(b'synthetic input')
    assert failure.value is error
