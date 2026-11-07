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
    assert pad(b"\x66\x90", False)                # 2-byte nop
    assert pad(b"\x0f\x1f\x00", False)            # 3-byte nop
    assert pad(b"\x0f\x1f\x40\x00", False)        # 4-byte nop
    assert pad(b"\x0f\x1f\x44\x00\x00\x90", False)  # 5-byte nop, then a plain nop
    assert not pad(b"\x90\x01", False)            # 0x01 is not filler
    assert not pad(b"\x0f\x1f", False)            # a truncated multi-byte nop
    assert not pad(b"", False)
    assert arch.get("x86_64").is_padding(b"\xcc\xcc", False)   # same filler on x86-64


def test_ppc_padding_is_ori_r0_r0_0():
    pad = arch.get("ppc").is_padding
    assert pad is not None
    assert pad(b"\x60\x00\x00\x00" * 2, True)     # nop = ori r0,r0,0, big-endian
    assert not pad(b"\x60\x00\x00\x01", True)     # not the nop word
    assert not pad(b"\x60\x00\x00", True)         # not word-aligned
    assert arch.get("ppc64").is_padding(b"\x60\x00\x00\x00", True)  # ppc64 reuses it


def test_arm_and_thumb_padding():
    pad = arch.get("arm").is_padding
    assert pad is not None and arch.get("thumb").is_padding is pad   # one reader serves both modes
    assert pad(b"\x00\x00\xa0\xe1" * 2, False)        # ARM nop mov r0,r0 (0xE1A00000), little-endian
    assert pad(b"\xc0\x46" * 3, False)                  # Thumb nop mov r8,r8 (0x46C0)
    assert pad(b"\x00\xbf" * 2, False)                  # Thumb-2 nop (0xBF00)
    assert pad(b"\x00" * 8, False)                       # zero fill
    assert not pad(b"\x00\x00\xa0\xe1\x01\x00\x00\x00", False)  # a real instruction after the nop
    assert not pad(b"", False)


def test_gba_console_reads_arm_and_thumb_padding():
    """The GBA console (arch "arm") now recognises its alignment padding, so a split head that is only Thumb or ARM
    nops can be retired instead of staying open."""
    from decompnet.consoles.gba import GBA
    assert GBA.is_padding(b"\xc0\x46" * 4)              # Thumb nops (the GBA is Thumb-heavy)
    assert GBA.is_padding(b"\x00\x00\xa0\xe1")        # an ARM nop
    assert not GBA.is_padding(b"\x01\x00\xa0\xe1")    # a real ARM instruction
