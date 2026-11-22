import struct

from decompnet.consoles.n64.code import bridge_loaded_prologues


def _fixture():
    image = bytearray(64)
    struct.pack_into('>5I', image, 0, 0x03e00008, 0x27bd0058, 0x3c02803d, 0x8c4231a0, 0x27bdfe98)
    found = [(0, 12, 0x80001000), (16, 32, 0x80001010)]
    return image, found


def test_join_scheduled_load_to_runtime_observed_frame():
    image, found = _fixture()
    result = bridge_loaded_prologues(image, found, [found[1]], log=lambda _: None)
    assert result == [(0, 8, 0x80001000), (8, 32, 0x80001008)]
    assert bridge_loaded_prologues(image, found, [], log=lambda _: None) == found


def test_keep_gap_if_interior_has_an_independent_entry():
    image, found = _fixture()
    struct.pack_into('>I', image, 32, 0x0c000000 | ((0x8000100c >> 2) & 0x3ffffff))
    assert bridge_loaded_prologues(image, found, [found[1]], [(32, 40, 0x80002000)], log=lambda _: None) == found
    image, found = _fixture()
    struct.pack_into('>I', image, 12, 0x8c4331a0)
    assert bridge_loaded_prologues(image, found, [found[1]], log=lambda _: None) == found
