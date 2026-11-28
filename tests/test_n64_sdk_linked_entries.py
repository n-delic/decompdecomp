"""Linked SDK fingerprints must be bound to the complete body and image."""
import hashlib
from types import SimpleNamespace

import pytest

from decompnet.consoles.n64 import sdk_linked


@pytest.mark.parametrize(('digest', 'size', 'name'), [
    ('8081d37a7463efd172c8f0b006ee7ce4fddb12826faab65073fafe16ce990dab', 16, '__osExceptionPreamble'),
    ('ffd9b4ef7ede2edbbad8f193da4f14f841318f70e870c0e4e54b992273702a1f', 32, '__ptExceptionPreamble'),
    ('bbf04453bd2414ee9dfe1663dd5f74f625126b8d485f2995bc644b438a8f6a71', 160, '__ptException'),
    ('54687027e918662c0f4fad3b0c02369bcb54450afefab18763aeb2a130f15c15', 112, '__osDisableInt'),
    ('56d0980fd0dbff05336c1bf6863ee9d7df5b5e914ed6bfa1e67cbb51531cc0d6', 160, 'osSetIntMask'),
])
def test_known_linked_sdk_identity_is_image_and_size_bound(monkeypatch, digest, size, name):
    image = '47ae795a2b12783a13e2b8d03439ce9a39fc826c'
    monkeypatch.setattr(sdk_linked.hashlib, 'sha256', lambda _: SimpleNamespace(hexdigest=lambda: digest))
    proof = sdk_linked.identify(bytes(size), image)
    assert proof['name'] == name and not proof['from_c'] and proof['bytes'] == size
    assert sdk_linked.identify(bytes(size), '0'*40) is None
    assert sdk_linked.identify(bytes(size + 4), image) is None


@pytest.mark.parametrize('image', [
    'cf806ff2603640a748fca5026ded28802f1f4a50',
    '0f07d05f6696deb9df26481881723517833a134c',
])
@pytest.mark.parametrize(('digest', 'size', 'name'), [
    ('48aaeb35564e08822c2bdb27216388c251c5694800d6ecdfcf7e097b9a755ac0', 28, '__d_to_ll'),
    ('f8bea959cb8ea987ad5d91b842cf546fb7d18ff8fc740c2e8417d4270aad84cd', 28, '__f_to_ll'),
    ('f827e7595e739486bed493f6652a7f4f147490ddcb4bf2134eaa2711cbaa426a', 160, '__d_to_ull'),
    ('c026001547b5a50c4149ac721e28d292d23ba56d3986c458544b586143391b6c', 156, '__f_to_ull'),
    ('3ba11f1a28a9802b21e5d730228be65dacb4301cb4c994f6806afc96828168e9', 24, '__ll_to_d'),
    ('2982a1e508fc621881ff0dd7f87fbd3b7c4887c2e03cfb32508c90fe86bb5fe5', 24, '__ll_to_f'),
])
def test_linked_conversion_identity_requires_the_image_and_full_body(monkeypatch, image, digest, size, name):
    monkeypatch.setattr(sdk_linked.hashlib, 'sha256', lambda _: SimpleNamespace(hexdigest=lambda: digest))
    proof = sdk_linked.identify(bytes(size), image)
    assert proof['name'] == name and proof['from_c'] and proof['bytes'] == size
    assert sdk_linked.identify(bytes(size), '0'*40) is None
    assert sdk_linked.identify(bytes(size+4), image) is None


def test_linked_identity_rejects_a_changed_body_or_image(monkeypatch):
    code = bytes(range(28))
    digest = hashlib.sha256(code).hexdigest()
    monkeypatch.setattr(sdk_linked, '_LIBULTRA', {'a'*40: {digest: ('conversion', 28, True, 'libc/llcvt.c')}})
    assert sdk_linked.identify(code, 'a'*40)['name'] == 'conversion'
    assert sdk_linked.identify(code[:-1] + b'\xff', 'a'*40) is None
    assert sdk_linked.identify(code, 'b'*40) is None


@pytest.mark.parametrize(('digest', 'size', 'name'), [
    ('48aaeb35564e08822c2bdb27216388c251c5694800d6ecdfcf7e097b9a755ac0', 28, '__d_to_ll'),
    ('f8bea959cb8ea987ad5d91b842cf546fb7d18ff8fc740c2e8417d4270aad84cd', 28, '__f_to_ll'),
    ('dcff610b8bf0a19cae292358d4dc3be7813ca66df72ab3e63e365fbd2665a814', 160, '__d_to_ull'),
    ('00dc74dfb22b0b1c45e63f0c92c27b961bc5b9e4dd13d1714fefb39495e8ce0e', 156, '__f_to_ull'),
    ('3ba11f1a28a9802b21e5d730228be65dacb4301cb4c994f6806afc96828168e9', 24, '__ll_to_d'),
    ('2982a1e508fc621881ff0dd7f87fbd3b7c4887c2e03cfb32508c90fe86bb5fe5', 24, '__ll_to_f'),
])
def test_another_linked_conversion_build_is_image_and_size_bound(monkeypatch, digest, size, name):
    image = '6da1a6a2bda687d50e798d80c342948ad1738202'
    monkeypatch.setattr(sdk_linked.hashlib, 'sha256', lambda _: SimpleNamespace(hexdigest=lambda: digest))
    proof = sdk_linked.identify(bytes(size), image)
    assert proof['name'] == name and proof['from_c'] and proof['bytes'] == size
    assert sdk_linked.identify(bytes(size), '0' * 40) is None
    assert sdk_linked.identify(bytes(size + 4), image) is None
