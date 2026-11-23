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


def test_a_pinned_name_is_left_alone(tmp_path):
    layout, rom = project(tmp_path, {"alInit": UNIT_DEFINES, "alAudioFrame": UNIT_USES})
    assert library_data.pair(tmp_path, {"alInit.c", "alAudioFrame.c"}, layout, {}, rom, IMAGE, {"alGlobals"})[0] == {}


def test_a_variable_no_file_defines_is_the_address_the_games_library_code_shares(tmp_path):
    """A game whose own alInit has no C declares alGlobals `external` everywhere and nothing defines it: the compiled C
    kept one copy and the game's machine code another, so the audio frame made a null command list and the game's
    audio thread stopped on 'null audio command lists'."""
    uses_init = UNIT_USES.replace("alAudioFrame", "alInit")
    layout, rom = project(tmp_path, {"alInit": uses_init, "alAudioFrame": UNIT_USES})
    shared, _ = library_data.pair(tmp_path, {"alInit.c", "alAudioFrame.c"}, layout, {}, rom, IMAGE, set())
    assert shared == {"alGlobals": 0x80034DD0}
    # the same through the library code the game's own C is (no unit of ultralib's is involved)
    (tmp_path / "y").mkdir()
    layout, rom = project(tmp_path / "y", {"alInit": uses_init, "alAudioFrame": UNIT_USES})
    assert library_data.pair(tmp_path / "y", set(), layout, {}, rom, IMAGE, set())[0] == {"alGlobals": 0x80034DD0}


def test_a_variable_some_file_defines_is_not_given_an_address_by_its_users(tmp_path):
    layout, rom = project(tmp_path, {"alInit": UNIT_USES.replace("alAudioFrame", "alInit"), "alAudioFrame": UNIT_USES})
    (tmp_path / "ll" / "elsewhere.ll").write_text("@alGlobals = global ptr null, align 4\n")
    assert library_data.pair(tmp_path, {"alInit.c", "alAudioFrame.c"}, layout, {}, rom, IMAGE, set())[0] == {}


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


def hi_lo(address):
    """The `lui` immediate and the signed low half an `addiu` adds to build `address`."""
    low = address & 0xFFFF
    return ((address >> 16) + (1 if low >= 0x8000 else 0)) & 0xFFFF, low


def build_address(register, address):
    hi, lo = hi_lo(address)
    return [lui(register, hi), addiu(register, register, lo)]


def constructor_project(tmp_path, constructors):
    """Each constructor `name: [function addresses it takes]` as a unit (a call to alFilterNew and the handlers'
    addresses as arguments) and as the game's own code; the handlers' own units are listed without a game function."""
    (tmp_path / "ll").mkdir(exist_ok=True)
    layout, rom, units = [], bytearray(0x1000), set()
    at = IMAGE
    handlers = {}
    for name, taken in constructors.items():
        ir = ["declare void @alFilterNew(ptr, ptr, ptr, i32)"]
        words = []
        for i, (handler, address) in enumerate(taken):
            ir.append(f"declare ptr @{handler}(ptr)")
            words += build_address(5 + i, address)
            handlers[handler] = address
        args = ", ".join(f"ptr @{h}" for h, _ in taken)
        ir.append(f"define void @{name}(ptr %f) {{\n  call void @alFilterNew(ptr %f, {args}, i32 4)\n  ret void\n}}")
        (tmp_path / "ll" / f"{name}.ll").write_text("\n".join(ir) + "\n")
        words += [JR_RA, NOP]
        layout.append({"name": name, "vram": at, "size": 4 * len(words)})
        rom += code(*words)
        units.add(name + ".c")
        at += 4 * len(words)
    return layout, bytes(rom), units, handlers, at


def test_an_unnamed_handler_the_constructor_takes_is_found_from_the_constructors_code(tmp_path):
    """alEnvmixerPull has no name in the game (large, not recognised), but alEnvmixerNew hands its address to
    alFilterNew, so its code builds exactly one function address no named function accounts for: the C taking the
    pull routine's address then gets it, not a wasm table index (`called 00000002, where no function is`)."""
    layout, rom, units, handlers, _ = constructor_project(
        tmp_path, {"alMixNew": [("alMixPull", 0x80002000), ("alMixParam", 0x80002100)]})
    layout += [{"name": "alMixParam", "vram": 0x80002100, "size": 0x40},  # named: known
               {"name": "func_80002000", "vram": 0x80002000, "size": 0x100}]  # not named
    (tmp_path / "ll" / "alMixPull.ll").write_text("define ptr @alMixPull(ptr %a) {\n}\n")
    (tmp_path / "ll" / "alMixParam.ll").write_text("define i32 @alMixParam(ptr %a) {\n}\n")
    units |= {"alMixPull.c", "alMixParam.c"}
    shared, own = library_data.pair(tmp_path, units, layout, {}, rom, IMAGE, set())
    assert own["alMixPull"] == 0x80002000 and shared == {}


def test_two_unnamed_functions_a_constructor_takes_are_not_guessed_apart(tmp_path):
    layout, rom, units, handlers, _ = constructor_project(
        tmp_path, {"alMixNew": [("alMixPull", 0x80002000), ("alMixOther", 0x80002100)]})
    layout += [{"name": "func_80002000", "vram": 0x80002000, "size": 0x100},
               {"name": "func_80002100", "vram": 0x80002100, "size": 0x100}]
    for name in ("alMixPull", "alMixOther"):
        (tmp_path / "ll" / f"{name}.ll").write_text(f"define ptr @{name}(ptr %a) {{\n}}\n")
        units.add(name + ".c")
    own = library_data.pair(tmp_path, units, layout, {}, rom, IMAGE, set())[1]
    assert "alMixPull" not in own and "alMixOther" not in own


def test_a_function_several_constructors_take_is_the_address_they_all_build(tmp_path):
    """alFilterNew's own address is built by each constructor as well; what every constructor shares is it, and
    what is left of each constructor's addresses is that constructor's own handler."""
    layout, rom, units, handlers, _ = constructor_project(tmp_path, {
        "alANew": [("alAPull", 0x80002000), ("alShared", 0x80003000)],
        "alBNew": [("alBPull", 0x80002100), ("alShared", 0x80003000)]})
    layout += [{"name": "func_80002000", "vram": 0x80002000, "size": 0x100},
               {"name": "func_80002100", "vram": 0x80002100, "size": 0x100},
               {"name": "func_80003000", "vram": 0x80003000, "size": 0x100}]
    for name in ("alAPull", "alBPull"):
        (tmp_path / "ll" / f"{name}.ll").write_text(f"define ptr @{name}(ptr %a) {{\n}}\n")
        units.add(name + ".c")
    own = library_data.pair(tmp_path, units, layout, {}, rom, IMAGE, set())[1]
    assert own["alAPull"] == 0x80002000 and own["alBPull"] == 0x80002100


def test_code_is_read_from_the_analysis_image_when_the_game_unpacks_it_at_run_time(tmp_path):
    """The cartridge holds a compressed game's code compressed: a function's bytes are read from the project's
    analysis image at its own ROM offset, as the layout of the boot copy doesn't hold for it."""
    layout, rom, units, handlers, _ = constructor_project(
        tmp_path, {"alMixNew": [("alMixPull", 0x80002000), ("alMixParam", 0x80002100)]})
    layout += [{"name": "alMixParam", "vram": 0x80002100, "size": 0x40},
               {"name": "func_80002000", "vram": 0x80002000, "size": 0x100}]
    (tmp_path / "ll" / "alMixPull.ll").write_text("define ptr @alMixPull(ptr %a) {\n}\n")
    (tmp_path / "ll" / "alMixParam.ll").write_text("define i32 @alMixParam(ptr %a) {\n}\n")
    units |= {"alMixPull.c", "alMixParam.c"}
    constructor = rom[0x1000:0x1000 + layout[0]["size"]]
    analysis = tmp_path / "analysis.z64"
    analysis.write_bytes(b"\0" * 0x300 + constructor)  # the unpacked copy, at an offset of its own
    garbage = b"\xff" * len(rom)  # the cartridge's bytes at the boot copy's offset are not code
    options = {"image": str(analysis), "offsets": {"alMixNew": 0x300}}
    assert "alMixPull" not in library_data.pair(tmp_path, units, layout, {}, garbage, IMAGE, set())[1]
    own = library_data.pair(tmp_path, units, layout, {}, garbage, IMAGE, set(), code_offsets=options)[1]
    assert own["alMixPull"] == 0x80002000


def test_a_static_handler_a_constructor_defines_for_itself_gets_the_games_address(tmp_path):
    """alCSPNew defines `static __CSPVoiceHandler` in its own file and stores its address in the player node; the
    game's copy of alCSPNew builds that address. It isn't a unit of its own, so it stayed without an address and the
    C stored a wasm table index (`1`): the game's audio frame, walking the player list, called address 1 (Blast Corps,
    once alGlobals became the game's and the player was really registered)."""
    layout, rom, units, handlers, _ = constructor_project(tmp_path, {"alSeqNew": [("__SeqHandler", 0x80002000)]})
    (tmp_path / "ll" / "alSeqNew.ll").write_text(
        "define void @alSeqNew(ptr %p) {\n  store ptr @__SeqHandler, ptr %p, align 4\n  ret void\n}\n"
        "define internal i32 @__SeqHandler(ptr %n) {\n  ret i32 0\n}\n")
    layout.append({"name": "func_80002000", "vram": 0x80002000, "size": 0x100})  # the game's own, unnamed
    own = library_data.pair(tmp_path, units, layout, {}, rom, IMAGE, set())[1]
    assert own["__SeqHandler"] == 0x80002000
    # a name something else already pins is left alone
    own = library_data.pair(tmp_path, units, layout, {}, rom, IMAGE, {"__SeqHandler"})[1]
    assert "__SeqHandler" not in own
