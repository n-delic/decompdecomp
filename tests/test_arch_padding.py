"""Each processor knows its own alignment padding (its nop/filler), so a function trimmed down to it can be
retired. Pure byte-level predicates -- no console or ROM needed. arm and thumb have their own mode-specific
readers (the ARM nop vs the Thumb nops), and the console picks between them by the function's mode."""

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
    assert pad(b"\x00" * 8, True)                 # a zero-filled gap is padding too (the PS3 splitter counts zero words)
    assert not pad(b"\x60\x00\x00\x01", True)     # not the nop word
    assert not pad(b"\x60\x00\x00", True)         # not word-aligned
    assert arch.get("ppc64").is_padding(b"\x60\x00\x00\x00", True)  # ppc64 reuses it


def test_arm_and_thumb_padding():
    arm_pad = arch.get("arm").is_padding
    thumb_pad = arch.get("thumb").is_padding
    assert arm_pad is not None and thumb_pad is not None and arm_pad is not thumb_pad  # a reader per mode
    assert arm_pad(b"\x00" * 8, False) and thumb_pad(b"\x00" * 8, False)   # zero fill is padding in either mode
    assert arm_pad(b"\x00\x00\xa0\xe1" * 2, False)     # ARM nop mov r0,r0 (0xE1A00000), little-endian
    assert thumb_pad(b"\xc0\x46" * 3, False)             # Thumb nop mov r8,r8 (0x46C0)
    assert thumb_pad(b"\x00\xbf" * 2, False)             # Thumb-2 nop (0xBF00)
    # the mode matters: an ARM nop's four bytes are not two Thumb nops, and two Thumb nops are not an ARM nop word
    assert not thumb_pad(b"\x00\x00\xa0\xe1", False)
    assert not arm_pad(b"\xc0\x46\xc0\x46", False)
    assert not arm_pad(b"\x00\x00\xa0\xe1\x01\x00\x00\x00", False)  # a real instruction after the nop
    assert not arm_pad(b"", False) and not thumb_pad(b"", False)


def test_gba_console_picks_the_padding_reader_by_mode():
    """The GBA console (arch "arm") recognises its alignment padding and chooses the ARM or Thumb reader from the
    function's mode -- so a split head that is only Thumb (or ARM) nops is retired, while a Thumb function whose
    bytes merely look like an ARM nop is not mistaken for padding (the review's regression)."""
    from decompnet.consoles.gba import GBA
    thumb_fn = {"meta": '{"thumb": true}'}
    arm_fn = {"meta": '{"thumb": false}'}
    assert GBA.is_padding(b"\xc0\x46" * 4, thumb_fn)       # Thumb nops under a Thumb function (the GBA is Thumb-heavy)
    assert GBA.is_padding(b"\x00\x00\xa0\xe1", arm_fn)   # an ARM nop under an ARM function
    assert GBA.is_padding(b"\x00" * 8, thumb_fn)         # zero fill, either mode
    assert not GBA.is_padding(b"\x00\x00\xa0\xe1", thumb_fn)  # ARM-nop bytes are NOT Thumb padding (movs r0,r0; ...)
    assert not GBA.is_padding(b"\x01\x00\xa0\xe1", arm_fn)    # a real ARM instruction


def test_gba_reads_an_old_arm_rows_mode_from_its_macro_not_defaulting_to_thumb():
    """The exact call `_cut` makes is `console.is_padding(code, fn)` with the function's stored row. An old ARM
    row whose meta carries no 'thumb' key keeps its mode in the luvdis macro (as `function_from_row` reads it);
    `target_arch` must read that too, or the ARM-nop padding is checked with the Thumb reader and the split
    sliver is never retired (the review's open GBA `_cut` point)."""
    from decompnet.consoles.gba import GBA
    arm_row = {"asm": "arm_func_start sub\n", "meta": "{}"}
    thumb_row = {"asm": "thumb_func_start sub\n", "meta": "{}"}
    assert GBA.target_arch(arm_row) == "arm" and GBA.target_arch(thumb_row) == "thumb"
    assert GBA.is_padding(b"\x00\x00\xa0\xe1", arm_row)        # ARM nop (0xE1A00000) under an ARM row: padding
    assert not GBA.is_padding(b"\x00\x00\xa0\xe1", thumb_row)  # same bytes under a Thumb row are not padding
