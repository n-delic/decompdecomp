"""Each processor knows its own alignment padding (its nop/filler), so a function trimmed down to it can be
retired. Pure byte-level predicates -- no console or ROM needed. arm/thumb have none yet (their heads stay open)."""

from decompnet import arch


def test_mips_padding_is_zero_words():
    pad = arch.get("mips").is_padding
    assert pad is not None
    assert pad(b"\x00" * 8, True)                 # nop = 0x00000000 (sll $zero)
    assert not pad(b"\x00\x00\x00\x01", True)     # real instruction
    assert not pad(b"", True)                     # empty is not padding


def test_x86_padding_is_nop_or_int3():
    pad = arch.get("x86").is_padding
    assert pad is not None
    assert pad(b"\x90\x90\xcc\x90", False)        # nop (0x90) / int3 (0xCC)
    assert not pad(b"\x90\x01", False)            # 0x01 is not filler
    assert not pad(b"", False)
    assert arch.get("x86_64").is_padding(b"\xcc\xcc", False)   # same filler on x86-64


def test_ppc_padding_is_ori_r0_r0_0():
    pad = arch.get("ppc").is_padding
    assert pad is not None
    assert pad(b"\x60\x00\x00\x00" * 2, True)     # nop = ori r0,r0,0, big-endian
    assert not pad(b"\x60\x00\x00\x01", True)     # not the nop word
    assert not pad(b"\x60\x00\x00", True)         # not word-aligned
    assert arch.get("ppc64").is_padding(b"\x60\x00\x00\x00", True)  # ppc64 reuses it


def test_arm_has_no_padding_reader_yet():
    assert arch.get("arm").is_padding is None     # follow-up: heads just stay open
    assert arch.get("thumb").is_padding is None
