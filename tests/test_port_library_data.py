"""The state ultralib's C shares is the game's variable at the game's address (port/wasm/library_data.py)."""
import struct

from decompnet.port.wasm import library_data

IMAGE = 0x80000400  # where the boot copies the cartridge's code (ROM offset 0x1000)


def lui(rt, imm):
    return 0x3C000000 | rt << 16 | imm


def lw(rt, offset, rs):
    return 0x8C000000 | rs << 21 | rt << 16 | offset & 0xFFFF


def sw(rt, offset, rs):
    return 0xAC000000 | rs << 21 | rt << 16 | offset & 0xFFFF


def addiu(rt, rs, imm):
    return 0x24000000 | rs << 21 | rt << 16 | imm & 0xFFFF


def ori(rt, rs, imm):
    return 0x34000000 | rs << 21 | rt << 16 | imm & 0xFFFF


JR_RA, NOP = 0x03E00008, 0


def code(*words):
    return b"".join(struct.pack(">I", w) for w in words)


def test_ll_names_tell_a_units_shared_variables_from_its_own():
    text = ("@alGlobals = hidden global ptr null, align 4\n"
            "@other = external global ptr, align 4\n"
            "@table = internal global [2 x i32] zeroinitializer\n"
            "@limit = hidden constant i32 7\n"
            "@.str = private constant [3 x i8] c\"ab\\00\"\n"
            "@alFilterNew = external global i8\n"
            "define void @f() {\n}\n")
    defined, referenced = library_data.ll_names(text, {"alFilterNew"})
    assert defined == {"alGlobals"} and referenced == {"other"}


def test_data_addresses_follow_lui_with_the_instruction_that_finishes_them():
    words = [lui(14, 0x8003), lw(14, 0x4DD0, 14),  # lw $t6, %lo(D_80034DD0)($t6)
             lui(1, 0x800B), sw(0, -0x2530, 1),  # sw $zero, %lo(D_800AAAD0)($at): a negative low half
             lui(8, 0x8004), addiu(8, 8, 0x10),  # addiu $t0, $t0, 0x10
             lui(9, 0x8005), ori(10, 9, 0x20),  # ori $t2, $t1, 0x20
             lui(11, 0x1234), lw(11, 4, 11),  # not RAM
             JR_RA, NOP]
    found = library_data.data_addresses(code(*words), 0x80000500, ([], []))
    assert found == {0x80034DD0, 0x800ADAD0, 0x80040010, 0x80050020}


def test_data_addresses_leave_out_addresses_inside_functions():
    words = [lui(14, 0x8000), addiu(14, 14, 0x600), JR_RA, NOP]
    assert library_data.data_addresses(code(*words), 0x80000500, ([0x80000600], [0x80000700])) == set()
    assert library_data.data_addresses(code(*words), 0x80000500, ([], [])) == {0x80000600}


def project(tmp_path, units):
    """A build folder with each unit's IR, and a cartridge with the game's own library functions in it."""
    (tmp_path / "ll").mkdir()
    for name, text in units.items():
        (tmp_path / "ll" / f"{name}.ll").write_text(text)
    layout, rom = [], bytearray(0x1000)
    at = IMAGE
    for name, words in (("alInit", [lui(14, 0x8003), lw(14, 0x4DD0, 14), lui(15, 0x8006), lw(15, 0x10, 15), JR_RA, NOP]),
                        ("alAudioFrame", [lui(14, 0x8003), lw(14, 0x4DD0, 14), lui(15, 0x8007), lw(15, 0x20, 15),
                                          JR_RA, NOP])):
        layout.append({"name": name, "vram": at, "size": 4 * len(words)})
        rom += code(*words)
        at += 4 * len(words)
    return layout, bytes(rom)


UNIT_DEFINES = "@alGlobals = hidden global ptr null, align 4\ndefine void @alInit(ptr %a) {\n}\n"
UNIT_USES = "@alGlobals = external global ptr, align 4\ndefine ptr @alAudioFrame() {\n}\n"


def test_a_variable_ultralib_defines_is_the_address_the_games_functions_share(tmp_path):
    layout, rom = project(tmp_path, {"alInit": UNIT_DEFINES, "alAudioFrame": UNIT_USES})
    shared, own = library_data.pair(tmp_path, {"alInit.c", "alAudioFrame.c"}, layout, {}, rom, IMAGE, set())
    assert shared == {"alGlobals": 0x80034DD0}
    assert own == {"alInit": IMAGE, "alAudioFrame": IMAGE + 24}  # the units' functions: the game's own addresses


def test_a_pinned_name_and_a_name_no_unit_defines_are_left_alone(tmp_path):
    layout, rom = project(tmp_path, {"alInit": UNIT_DEFINES, "alAudioFrame": UNIT_USES})
    assert library_data.pair(tmp_path, {"alInit.c", "alAudioFrame.c"}, layout, {}, rom, IMAGE, {"alGlobals"})[0] == {}
    # the host's or the game's own variable: declared by units, defined by none
    (tmp_path / "x").mkdir()
    layout, rom = project(tmp_path / "x", {"alInit": UNIT_USES.replace("alAudioFrame", "alInit"),
                                           "alAudioFrame": UNIT_USES})
    assert library_data.pair(tmp_path / "x", {"alInit.c", "alAudioFrame.c"}, layout, {}, rom, IMAGE, set())[0] == {}


def test_one_function_with_several_data_addresses_is_not_a_pairing(tmp_path):
    layout, rom = project(tmp_path, {"alInit": UNIT_DEFINES})
    assert library_data.pair(tmp_path, {"alInit.c"}, layout, {}, rom, IMAGE, set())[0] == {}


def test_a_library_name_found_by_detection_pairs_through_the_games_function(tmp_path):
    layout, rom = project(tmp_path, {"alInit": UNIT_DEFINES, "alAudioFrame": UNIT_USES})
    for f in layout:  # the game's functions, not yet renamed to the library's names
        f["name"] = "func_" + f["name"]
    names = {"func_alInit": "alInit", "func_alAudioFrame": "alAudioFrame"}
    shared, own = library_data.pair(tmp_path, {"alInit.c", "alAudioFrame.c"}, layout, names, rom, IMAGE, set())
    assert shared == {"alGlobals": 0x80034DD0} and own["alInit"] == IMAGE


def test_an_address_two_names_would_share_is_not_given(tmp_path):
    two = "@alGlobals = hidden global ptr null, align 4\n@alOther = hidden global ptr null, align 4\n"
    uses = "@alGlobals = external global ptr, align 4\n@alOther = external global ptr, align 4\n"
    layout, rom = project(tmp_path, {"alInit": two + "define void @alInit() {\n}\n",
                                     "alAudioFrame": uses + "define void @alAudioFrame() {\n}\n"})
    assert library_data.pair(tmp_path, {"alInit.c", "alAudioFrame.c"}, layout, {}, rom, IMAGE, set())[0] == {}


def test_a_short_cartridge_gives_no_addresses(tmp_path):
    layout, rom = project(tmp_path, {"alInit": UNIT_DEFINES, "alAudioFrame": UNIT_USES})
    assert library_data.pair(tmp_path, {"alInit.c", "alAudioFrame.c"}, layout, {}, rom[:0x1008], IMAGE, set())[0] == {}
