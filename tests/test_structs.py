"""Struct layouts guessed from the loads and stores through pointers (structs.py, arch/mips_fields.py), on
hand-assembled MIPS code, so no compiler is needed."""

import functools
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import arch, consoles, structs, toolchains
from decompnet.app.engine import RomNetwork
from decompnet.arch import mips_fields

ZERO, V0, V1, A0, A1, S0, GP, SP, RA = 0, 2, 3, 4, 5, 16, 28, 29, 31
LW, LH, LHU, LB, SB, SW, SH, LWC1, ADDIU, LUI, BNE = 0x23, 0x21, 0x25, 0x20, 0x28, 0x2B, 0x29, 0x31, 0x09, 0x0F, 0x05
ADDU = 0x21
JR_RA, NOP = 0x03E00008, 0
LO16, GPREL16 = mips_fields.R_MIPS_LO16, mips_fields.R_MIPS_GPREL16


def i(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def move(rd, rs):
    return (rs << 21) | (rd << 11) | ADDU


def jal(target):
    return (3 << 26) | ((target >> 2) & 0x3FFFFFF)


def code(*words):
    return struct.pack(f"<{len(words)}I", *words)


def recover(functions):
    return structs.recover(functions, functools.partial(mips_fields.read_facts, big_endian=False), lambda a: a)


def fields(found, key, path=()):
    lay = found.layout(found.type_of(key, path))
    return {f.offset: ("pointer" if f.points_to is not None else f.ctype(), len(f.functions)) for f in lay.fields}


READ, CALLER = 0x100000, 0x100100
FUNCTIONS = [
    # READ(p): p->s_04, p->f_10, p->ptr_14->h_08
    (READ, code(i(LW, A0, V0, 4), i(LWC1, A0, 0, 0x10), i(LW, A0, V1, 0x14), i(LH, V1, V1, 8), JR_RA, NOP), []),
    # CALLER(p): p->u_02; READ(p) with a0 set in the call's delay slot; p->x_18 = 0 through the saved register
    (CALLER, code(move(S0, A0), i(LHU, A0, V0, 2), jal(READ), move(A0, S0), i(SW, S0, ZERO, 0x18), JR_RA, NOP),
     [[8, mips_fields.R_MIPS_26, READ]]),
]


def test_a_layout_from_one_functions_loads_and_the_pointer_field_it_follows():
    found = recover(FUNCTIONS[:1])
    assert fields(found, (structs.ARGUMENT, READ, 0)) == {4: ("s32", 1), 0x10: ("f32", 1), 0x14: ("pointer", 1)}
    assert fields(found, (structs.ARGUMENT, READ, 0), [0x14]) == {8: ("s16", 1)}


def test_a_pointer_handed_to_a_callee_is_the_callees_type():
    found = recover(FUNCTIONS)
    assert found.type_of((structs.ARGUMENT, CALLER, 0)) == found.type_of((structs.ARGUMENT, READ, 0))
    assert fields(found, (structs.ARGUMENT, CALLER, 0)) == {
        2: ("u16", 1), 4: ("s32", 1), 0x10: ("f32", 1), 0x14: ("pointer", 1), 0x18: ("s32", 1)}
    lay = found.layout(found.type_of((structs.ARGUMENT, CALLER, 0)))
    assert lay.functions == {READ, CALLER} and found.name(lay.root, lambda a: f"f_{a:X}") == "S_f_100000_arg0"
    text = structs.c_text(lay, "S_f_100000_arg0", lambda t: "S_inner")
    assert "/* 0x00 */ u8 unk_00[0x2];" in text and "/* 0x02 */ u16 unk_02; /* 1 function */" in text
    assert "/* 0x14 */ struct S_inner *unk_14;" in text and "/* 0x08 */ u8 unk_08[0x8];" in text
    assert text.endswith("} S_f_100000_arg0; /* at least 0x1C bytes */")
    assert structs.summary(lay, 2) == "+0x2 u16 (1 function), +0x4 s32 (1), and 3 more"


def _caller(at, offset, op=LW, callee=READ):
    """A function that reads its argument at `offset` and hands it to `callee`."""
    return (at, code(i(op, A0, V0, offset), jal(callee), NOP, JR_RA, NOP), [[4, mips_fields.R_MIPS_26, callee]])


def test_a_callee_with_many_callers_keeps_its_own_type():
    """A base class's method, or CVector's, is called with everything: its callers' types aren't made one."""
    few = [_caller(0x200000 + 0x100 * n, 0x40 + 4 * n) for n in range(structs.HUB_CALLERS)]
    found = recover(FUNCTIONS[:1] + few)
    assert len({found.type_of((structs.ARGUMENT, at, 0)) for at, _, _ in few}) == 1
    many = [_caller(0x200000 + 0x100 * n, 0x40 + 4 * n) for n in range(structs.HUB_CALLERS + 1)]
    found = recover(FUNCTIONS[:1] + many)
    assert len({found.type_of((structs.ARGUMENT, at, 0)) for at, _, _ in many}) == len(many)


def test_types_that_disagree_on_a_width_stay_apart():
    found = recover(FUNCTIONS[:1] + [_caller(0x200000, 4, op=LH)])  # READ reads +4 as a word, this one as a halfword
    assert found.type_of((structs.ARGUMENT, 0x200000, 0)) != found.type_of((structs.ARGUMENT, READ, 0))


WALK = 0x300000


def test_a_function_that_walks_its_argument_doesnt_join_its_callers():
    """memset(p, 0, n) is handed every kind of struct: walking the pointer says it isn't one of them."""
    memset = (WALK, code(i(SB, A0, ZERO, 0), i(ADDIU, A0, A0, 1), i(ADDIU, A1, A1, -1), i(BNE, A1, ZERO, -4), NOP,
                         JR_RA, NOP), [])
    found = recover([memset, _caller(0x200000, 4, callee=WALK), _caller(0x200100, 0x20, callee=WALK)])
    assert found.type_of((structs.ARGUMENT, 0x200000, 0)) != found.type_of((structs.ARGUMENT, 0x200100, 0))
    assert fields(found, (structs.ARGUMENT, WALK, 0)) == {0: ("s8", 1)}


def test_globals_and_what_they_point_to():
    """Two functions read a $gp-relative pointer and a field through it; a global's address handed to a callee is
    its type; a function's address (a callback) is no struct."""
    gp_pointer, table = 0x500000, 0x500100
    readers = [(0x200000 + 0x100 * n, code(i(LW, GP, V0, 0), i(LW, V0, V1, 0x10 + 4 * n), JR_RA, NOP),
                [[0, GPREL16, gp_pointer]]) for n in range(2)]
    hand = (0x200200, code(i(LUI, 0, A0, 0x50), i(ADDIU, A0, A0, 0x100), jal(READ), NOP, JR_RA, NOP),
            [[4, LO16, table], [8, mips_fields.R_MIPS_26, READ]])
    callback = (0x200300, code(i(LUI, 0, A0, 0x10), i(ADDIU, A0, A0, 0), jal(READ), NOP, JR_RA, NOP),
                [[4, LO16, READ], [8, mips_fields.R_MIPS_26, READ]])
    found = recover(FUNCTIONS[:1] + readers + [hand, callback])
    assert fields(found, (structs.GLOBAL, gp_pointer), [0]) == {0x10: ("s32", 1), 0x14: ("s32", 1)}
    assert found.type_of((structs.GLOBAL, table)) == found.type_of((structs.ARGUMENT, READ, 0))
    assert found.type_of((structs.GLOBAL, READ)) is None


def test_a_pointer_spilled_to_the_stack_is_still_the_argument():
    spill = (0x200000, code(i(SW, SP, A0, 0x20), jal(0x400000), NOP, i(LW, SP, A0, 0x20), i(LH, A0, V0, 8), JR_RA, NOP),
             [])
    assert fields(recover([spill]), (structs.ARGUMENT, 0x200000, 0)) == {8: ("s16", 1)}


def test_the_types_agents_gave_an_argument_in_matched_code():
    source = "s32 helper(void);\nvoid func_1(const CPed *ped, f32 speed, u8 flags) {\n    ped->x = speed;\n}\n"
    assert structs.parameter_types(source, "func_1") == ["CPed *", "f32", "u8"]
    assert structs.argument_type(source, "func_1", 0) == "CPed *"
    assert structs.argument_type(source, "func_1", 2) is None  # after a float: where it travels depends on the ABI
    assert structs.parameter_types("void func_2(void) {}", "func_2") == []


def test_mips_has_the_reader():
    mips = arch.get("mips")
    assert arch.STRUCTS in mips.features and mips.field_accesses is mips_fields.read_facts
    assert arch.get("ppc").field_accesses is None


def test_a_header_gives_a_types_definition():
    header = "typedef struct CPed CPed;\nstruct CPed {\n    s32 hp; /* 0x00 */\n};\ntypedef struct {\n    u8 a;\n} Pool;\n"
    assert structs.definition(header, "CPed") == "struct CPed {\n    s32 hp; /* 0x00 */\n};"
    assert structs.definition(header, "Pool") == "typedef struct {\n    u8 a;\n} Pool;"
    assert structs.definition(header, "CVehicle") is None
    assert structs.type_name("const struct CPed *") == "CPed" and structs.type_name("u8 *") is None
    assert structs.type_name("s32") is None


# ----------------------------------------------------------------------------- the demo games, with their compilers
# Demo Quest's Entity (projects/demo-n64/original/game.h), built by each MIPS console's compiler: the guess for
# entity_update_physics's argument is the struct the source has, field for field.

ENTITY = {0x00: "s16", 0x02: "u16", 0x04: "s32", 0x08: "s32", 0x0C: "s32", 0x10: "s32", 0x14: "s16", 0x16: "s16",
          0x18: "s32"}
MIPS_DEMOS = [c for c in consoles.startable() if c.demo and arch.STRUCTS in c.features]
DEMO_SOURCE = Path(__file__).resolve().parent.parent / "projects" / "demo-n64" / "original" / "game.c"
DEMO_DEFINES = "#define GRAVITY 2\n#define MAX_FALL_SPEED 24\n#define FLOOR_Y 448\n#define ENTITY_FLAG_GROUNDED 0x2\n"


@pytest.fixture(scope="module", params=MIPS_DEMOS, ids=[c.id for c in MIPS_DEMOS])
def demo_game(request, tmp_path_factory):
    from .test_consoles import ROOT, _missing

    console = request.param
    if (missing := _missing(console)):
        pytest.skip(f"{console.name}'s demo needs {', '.join(missing)}")
    folder = ROOT / "projects" / console.demo
    if not (folder / "build" / "demo.json").exists():
        subprocess.run([sys.executable, str(folder / "build.py")], check=True)
    built = json.loads((folder / "build" / "demo.json").read_text())
    work = tmp_path_factory.mktemp(console.id)
    net = RomNetwork(work / "app.db", work)
    slug = net.import_rom(folder / "build" / built["game"], confirmed=True, log=lambda *_: None)
    return net, slug, built["symbols"]


def _name(net, slug, symbols, name):
    pid = net.project_row(slug)["id"]
    return net.store.one("SELECT name FROM functions WHERE project_id=? AND vram=?", pid, symbols[name])["name"]


def test_the_guess_for_a_demos_struct_is_the_struct_its_source_has(demo_game):
    net, slug, symbols = demo_game
    name = _name(net, slug, symbols, "entity_update_physics")
    text = net.guess_struct(slug, name, "arg0")
    guessed = {int(offset, 16): ctype for offset, ctype in re.findall(r"/\* 0x([0-9A-F]+) \*/ (\w+) unk_", text)}
    assert guessed == ENTITY, text
    assert "The same type: the global" in text  # g_entities, the array entity_update's loop walks
    brief = net.get_function(name, slug)
    assert "### Likely struct layouts" in brief and "- arg0: S_" in brief and "+0x2 u16" in brief
    assert "No loads or stores through arg0" in net.guess_struct(slug, _name(net, slug, symbols, "abs_s32"))

    # once a function of the type matches with a struct of its own, the others are told its name
    from decompnet.forks import csource

    game = DEMO_SOURCE.parent
    entity = structs.definition((game / "game.h").read_text(), "Entity")
    body = csource.get(DEMO_SOURCE.read_text(), "entity_update_physics").replace("entity_update_physics(", f"{name}(", 1)
    agent, _ = net.local_agent("struct-agent")
    net.claim(agent, slug, name)
    assert net.attempt(agent, name, DEMO_DEFINES + entity + "\n" + body, slug)["matched"]
    text = net.guess_struct(slug, _name(net, slug, symbols, "entity_collides"), "arg0")
    assert f"Matched code already calls it Entity ({name} arg0), defined in its own source" in text, text


@pytest.mark.skipif(toolchains.installed("ee-gcc-2.96") is None or toolchains.installed("binutils-mips") is None,
                    reason="needs ee-gcc-2.96 and binutils-mips to build the PS2 demo")
def test_a_game_with_debug_information_gets_its_real_type(tmp_path):
    from .test_ps2_debug import _import_debug_game

    net, slug, _ = _import_debug_game(tmp_path)
    text = net.guess_struct(slug, "entity_update_physics", "arg0")
    assert "`Entity *`: the real type" in text and "struct Entity {" in text, text
    assert "### Likely struct layouts" not in net.get_function("entity_update_physics", slug)
