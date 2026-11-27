"""Linked SDK fingerprints must be bound to the complete body and image."""
from types import SimpleNamespace

import pytest

from decompnet.consoles.n64 import sdk_linked


@pytest.mark.parametrize(('digest', 'size', 'name'), [
    ('8081d37a7463efd172c8f0b006ee7ce4fddb12826faab65073fafe16ce990dab', 16, '__osExceptionPreamble'),
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
