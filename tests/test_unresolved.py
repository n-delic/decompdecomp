"""Which unresolved names in an LTCG harness link can change a function's code (forks/unresolved.py). Measured on
the LTCG demo (each reference left unresolved, then defined in the link): standard-convention callees change nothing;
callees the original passes registers or inlines do; library code must stay unresolved; data may."""

from decompnet.consoles.pc.scratch import DIR32, REL32
from decompnet.forks import convention, unresolved


class _Image:
    def __init__(self, code: dict[int, bytes]):
        self.code = code

    def read(self, addr: int, n: int) -> bytes:
        for start, data in self.code.items():
            if start <= addr < start + len(data):
                return data[addr - start:addr - start + n]
        return b"\0" * n


def _program():
    # caller 0x11000: call A (esi in), call B (__thiscall), call L (library), mov eax, [D]; ret
    caller = bytes.fromhex("e8 fb000000" "e8 f6010000" "e8 f1030000" "a1 00f20100" "c3")
    a = bytes.fromhex("8b06c3")  # mov eax, [esi]; ret: a register parameter of LTCG's own
    b = bytes.fromhex("8b01c20400")  # mov eax, [ecx]; ret 4: __thiscall
    c = bytes.fromhex("8b07c3")  # inlined into the caller: no call to it there
    lib = bytes.fromhex("8b442404c3")  # mov eax, [esp+4]; ret
    code = {0x11000: caller, 0x11100: a, 0x11200: b, 0x11300: c, 0x11400: lib}
    relocs = {0x11000: [[1, REL32, "_func_00011100", 0], [6, REL32, "_func_00011200@4", 0],
                        [11, REL32, "_sprintf", 0], [16, DIR32, "_D_0001F200", 0]]}
    names = {0x11000: "_func_00011000", 0x11100: "_func_00011100", 0x11200: "_func_00011200@4",
             0x11300: "_func_00011300", 0x11400: "_sprintf"}
    info = {"units": {"u": [{"name": names[a_], "addr": a_, "size": len(d), "relocs": relocs.get(a_, [])}
                            for a_, d in code.items()]}}
    return convention.index(_Image(code), info)


def test_names_as_source_writes_them():
    assert unresolved.identifier("?func_000115A0@Accum@@QAEXPBUParticle@@H@Z") == "func_000115A0"
    assert unresolved.identifier("_D_004AE1DB") == "D_004AE1DB"
    assert unresolved.identifier("_func_00011130@8") == "func_00011130"
    assert unresolved.identifier("@D3DDevice_SetPalette@8") == "D3DDevice_SetPalette"
    assert unresolved.identifier("??0CFoo@@QAE@XZ") == "CFoo"
    assert unresolved.identifier("?g_3EAD58@@3MA") == "g_3EAD58"
    assert unresolved.is_data_symbol("?D_003B168C@@3MB") and unresolved.is_data_symbol("?s@C@@2HA")
    assert unresolved.is_data_symbol("?ClampedDot@@YAMABUCVec4@@0@Z") is False
    assert unresolved.is_data_symbol("_D_0073C610") is None


def test_which_unresolved_names_change_the_code():
    prog = _program()
    kind = lambda s, lib=frozenset(): unresolved.classify(prog, 0x11000, s, lib)  # noqa: E731
    # data may change the code under LTCG: define it with its real type, never volatile
    assert (kind("_D_0001F200").kind, kind("_D_0001F200").matters) == ("data", None)
    assert "real type" in kind("_D_0001F200").why and "volatile" in kind("_D_0001F200").why
    assert kind("?D_003B168C@@3MB").kind == "data" and kind("?g_3EAD58@@3MA").matters is None
    a = kind("?func_00011100@@YAXXZ")  # the original passes it esi
    assert (a.kind, a.matters) == ("register", True) and "esi" in a.why and "standins_for" in a.why
    b = kind("?func_00011200@Accum@@QAEHH@Z")  # __thiscall: ecx is `this`
    assert (b.kind, b.matters) == ("standard", False)
    assert kind("?func_00011200@@YAHH@Z").matters is True  # declared a free function: ecx isn't standard then
    c = kind("?func_00011300@@YAXXZ")
    assert (c.kind, c.matters) == ("inlined", True)
    lib = kind("_sprintf", {0x11400})
    assert (lib.kind, lib.matters) == ("library", False) and "never stand it in" in lib.why
    assert kind("_XMemAlloc@8").matters is None and "library code" in kind("_XMemAlloc@8").why
    assert kind("?ClampedDot@@YAMABUCVec4@@0@Z").matters is None


def test_a_summary_names_what_to_do_first_and_says_the_rest_is_harmless():
    prog = _program()
    code = "void f(Accum *a) { func_00011100(); D_0001F200 = a->func_00011200(1); }"
    names = ["?func_00011100@@YAXXZ", "?func_00011200@Accum@@QAEHH@Z", "_D_0001F200", "_D_00099999"]
    used = unresolved.used_by(code, names)
    assert used == names[:3]
    lines = unresolved.summary([unresolved.classify(prog, 0x11000, s) for s in used], "u", len(names) - len(used))
    text = "\n".join(lines)
    assert lines[0].startswith("Unresolved in the harness link and changing this function's code (do it now, in this "
                               "claim: edit_file harness/u.<name>.cpp")
    assert "- func_00011100 (the original passes it esi" in text
    assert "harmless (no reason to stop or release" in text and "func_00011200" in text
    assert "Unresolved, may change this function's code:\n- D_0001F200 (data:" in text
    assert "1 more unresolved names are used by other functions of u" in text
    assert unresolved.summary([], "u") == []


def test_standins_for_a_unit_shape_what_it_calls_and_read_the_driver_calls_off_the_call_sites():
    from decompnet.forks import standins

    # the caller is the unit's: the stand-ins are for A and B (and the library function) outside it
    prog = _program()
    for a in (0x11100, 0x11200, 0x11300, 0x11400):
        prog.functions[a]["unit"] = "other"
    prog.functions[0x11000]["unit"] = "u"
    s = standins.skeleton(prog, "u")
    assert "float D_0001F200" not in s["standins"] and "int D_0001F200;" in s["standins"]  # (read as 4 bytes)
    assert "int func_00011100(int eax)" not in s["standins"] and "int func_00011100(int esi) { return 0; }" in s["standins"]
    assert "int func_00011200(int arg1);" in s["externs"]  # `ret 4`: one stack argument
    assert "HarnessEntry" in s["driver"] and "func_00011000: " in s["driver"]
