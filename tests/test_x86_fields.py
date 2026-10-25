"""Struct layouts from x86 code (arch/x86_fields.py, structs.py): `this` in ecx, arguments on the stack read through
esp or an ebp frame, the stack after calls, walks. The code is assembled by hand here, so no compiler is needed."""

import functools
import struct

from decompnet import arch, structs
from decompnet.arch import x86_fields

READ, CALLER, STD, AFTER, WALK, PROBE, CHKSTK, RETURNS = (0x401000, 0x401100, 0x401200, 0x401300, 0x401400,
                                                          0x401500, 0x401600, 0x401700)
RET = b"\xC3"
DATA = 0x500000  # where the globals are


def call(at: int, target: int) -> bytes:
    """`call target`, the instruction at `at`."""
    return b"\xE8" + struct.pack("<i", target - (at + 5))


def recover(functions, pops=None):
    read = functools.partial(x86_fields.read_facts, pops=pops or {})
    return structs.recover([(a, c, []) for a, c in functions], read, lambda a: a if a >= DATA else None)


def fields(found, key, path=()):
    lay = found.layout(found.type_of(key, path))
    return {f.offset: ("pointer" if f.points_to is not None else f.ctype(), len(f.functions)) for f in lay.fields}


# READ(p), __cdecl: p->s16_02, p->f32_10, p->ptr_14->u8_08
READ_CODE = (b"\x8B\x44\x24\x04"  # mov eax, [esp+4]
             b"\x0F\xBF\x48\x02"  # movsx ecx, word ptr [eax+2]
             b"\x8B\x50\x14"  # mov edx, [eax+0x14]
             b"\xD9\x40\x10"  # fld dword ptr [eax+0x10]
             b"\x0F\xB6\x4A\x08" + RET)  # movzx ecx, byte ptr [edx+8]
# CALLER, a method: saves esi, reads this->04, hands `this` to READ (pushed), pops it itself, writes this->18
CALLER_CODE = (b"\x56\x8B\xF1\x8B\x46\x04\x56" + call(CALLER + 7, READ) +  # push esi; mov esi, ecx; mov eax, [esi+4]
               b"\x83\xC4\x04"  # add esp, 4
               b"\xC7\x46\x18\x00\x00\x00\x00"  # mov dword ptr [esi+0x18], 0
               b"\x5E" + RET)  # pop esi


def test_a_layout_from_the_stack_argument_and_the_pointer_field_it_follows():
    found = recover([(READ, READ_CODE)])
    assert fields(found, (structs.ARGUMENT, READ, 0)) == {2: ("s16", 1), 0x10: ("f32", 1), 0x14: ("pointer", 1)}
    assert fields(found, (structs.ARGUMENT, READ, 0), [0x14]) == {8: ("u8", 1)}


def test_this_in_ecx_handed_to_a_callee_is_the_callees_type():
    found = recover([(READ, READ_CODE), (CALLER, CALLER_CODE)])
    assert found.type_of((structs.ARGUMENT, CALLER, structs.THIS)) == found.type_of((structs.ARGUMENT, READ, 0))
    assert fields(found, (structs.ARGUMENT, CALLER, structs.THIS)) == {
        2: ("s16", 1), 4: ("s32", 1), 0x10: ("f32", 1), 0x14: ("pointer", 1), 0x18: ("s32", 1)}
    assert found.name(found.type_of((structs.ARGUMENT, CALLER, structs.THIS)), lambda a: f"f_{a:X}") == "S_f_401000_arg0"
    assert structs.argument_name(structs.THIS) == "this"


def test_after_a_call_the_stack_is_where_the_callee_leaves_it():
    """`push 5; call STD; mov eax, [esp+4]`: arg0 only if STD popped its argument (__stdcall, `ret 4`), which its
    decorated name says; a __cdecl callee leaves it to the caller."""
    after = (AFTER, b"\x6A\x05" + call(AFTER + 2, STD) + b"\x8B\x44\x24\x04\x8B\x40\x20" + RET)
    std = (STD, b"\xC2\x04\x00")
    found = recover([after, std], pops={STD: 4})
    assert fields(found, (structs.ARGUMENT, AFTER, 0)) == {0x20: ("s32", 1)}
    found = recover([after, std], pops={STD: 0})
    assert found.type_of((structs.ARGUMENT, AFTER, 0)) is None
    found = recover([after, std])  # not known (a virtual call, an import): what was pushed for it, no `add esp` after
    assert fields(found, (structs.ARGUMENT, AFTER, 0)) == {0x20: ("s32", 1)}
    assert x86_fields.pops("_func_00401200@4") == 4 and x86_fields.pops("_func_00401200") == 0
    assert x86_fields.pops("@func_00401200@12") == 4 and x86_fields.pops("??0Town@@QAE@XZ") is None


def test_an_ebp_frame_a_big_frames_probe_and_what_a_function_returns():
    frame = (READ, b"\x55\x8B\xEC\x8B\x45\x0C\x66\x8B\x50\x06\x5D" + RET)  # push ebp; mov ebp, esp; [ebp+0xc]: arg1
    assert fields(recover([frame]), (structs.ARGUMENT, READ, 1)) == {6: ("s16", 1)}
    # mov eax, 0x1000; call __chkstk (it moves esp down by eax); mov eax, [esp+0x1004]: arg0
    probe = (PROBE, b"\xB8\x00\x10\x00\x00" + call(PROBE + 5, CHKSTK) + b"\x8B\x84\x24\x04\x10\x00\x00\x8B\x40\x08" + RET)
    found = recover([probe, (CHKSTK, RET)], pops={CHKSTK: 0})
    assert fields(found, (structs.ARGUMENT, PROBE, 0)) == {8: ("s32", 1)}
    returns = (RETURNS, b"\x8B\x41\x0C\x8B\xC1" + RET)  # mov eax, [ecx+0xc]; mov eax, ecx: returns `this`
    found = recover([returns])
    assert found.type_of((structs.RETURN, RETURNS)) == found.type_of((structs.ARGUMENT, RETURNS, structs.THIS))


def test_a_function_that_walks_its_argument_doesnt_join_its_callers():
    """memset(p, 0, n): `mov byte ptr [eax], 0; inc eax` in a loop is handed every kind of struct."""
    walk = (WALK, b"\x8B\x44\x24\x04\xC6\x00\x00\x40\x4A\x75\xF9" + RET)
    callers = [(0x402000 + 0x100 * n, b"\x8B\x44\x24\x04\x8B\x48" + bytes([4 + 4 * n]) + b"\x50"
                + call(0x402000 + 0x100 * n + 8, WALK) + b"\x83\xC4\x04" + RET) for n in range(2)]
    found = recover([walk, *callers])
    assert x86_fields.read_facts(WALK, walk[1], []).walked == {(structs.ARGUMENT, 0)}
    assert found.type_of((structs.ARGUMENT, 0x402000, 0)) != found.type_of((structs.ARGUMENT, 0x402100, 0))


def test_x86_has_the_reader():
    x86 = arch.get("x86")
    assert arch.STRUCTS in x86.features and x86.field_accesses is x86_fields.read_facts


def test_a_vtable_stored_through_a_register_it_doesnt_follow_is_still_a_store():
    """An object from an import's operator new, built inline (`mov dword ptr [ebx], offset vtable`): ebx comes from
    nowhere the reader follows, and the store still names the class (classes.from_facts)."""
    code = b"\xC7\x03" + struct.pack("<I", DATA + 0x10) + RET
    facts = x86_fields.read_facts(READ, code, [], datum=lambda a: a if a >= DATA else None)
    assert facts.stores == [((structs.CALL, 0, None), 0, (structs.GLOBAL, DATA + 0x10))]
