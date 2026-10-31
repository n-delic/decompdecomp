"""Member functions and their object types, recovered from a PC or Xbox program's code (forks/methods.py).

The program is hand-assembled: a struct whose two functions are called on one global object (`mov ecx, offset g`),
a class whose constructor stores its vtable, a function of two objects (Dot), one walking an array, and the same
struct's function taking `this` in ESI, as link-time code generation passes it.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from decompnet.consoles.pc.scratch import REL32
from decompnet.forks import convention, methods, standins

from .test_ltcg import BASE, _pe

OBJ, VTBL = BASE + 0x2000, BASE + 0x2010
CODE = {
    # A (Accum::Add): this in ecx, [this] += arg, this->f8 = this->f4 (floats); ret 4
    0x1000: bytes.fromhex("8b01" "03442404" "8901" "d94104" "d95908" "c20400"),
    0x1020: bytes.fromhex("8b410c" "c3"),  # M: return this->fC
    # main: A(&g, 5) and M(&g), `this` loaded with the global's address
    0x1040: bytes.fromhex("b900200010" "6a05" "e8b4ffffff" "b900200010" "e8caffffff" "c3"),
    0x1060: bytes.fromhex("8b4c2404" "8b4104" "c3"),  # F(p): a __cdecl function reading p->f4
    0x1080: bytes.fromhex("8bc1" "c70010200010" "c7400400000000" "c3"),  # C: the constructor, stores the vtable
    0x10A0: bytes.fromhex("d94108" "c3"),  # V1: return this->f8 (float)
    0x10B0: bytes.fromhex("8b4104" "c3"),  # V2: return this->f4
    0x10E0: bytes.fromhex("8b06" "03c2" "8906" "c3"),  # Add2 (LTCG): this in esi, k in edx
    0x1100: bytes.fromhex("be00200010" "ba03000000" "e8d1ffffff" "c3"),  # main2: Add2(&g, 3), as LTCG calls it
    0x1120: bytes.fromhex("d901" "d80a" "d94104" "d84a04" "dec1" "c3"),  # Dot(a=ecx, b=edx)
    0x1140: bytes.fromhex("33c0" "0306" "83c604" "4f" "75f8" "c3"),  # Sum(p=esi, n=edi): walks p
}
NAMES = {0x1000: "_A@4", 0x1020: "_M", 0x1040: "_main", 0x1060: "_F", 0x1080: "_C", 0x10A0: "_V1", 0x10B0: "_V2",
         0x10E0: "_Add2", 0x1100: "_main2", 0x1120: "_Dot", 0x1140: "_Sum"}


def _program(tmp_path: Path, ltcg: bool) -> tuple[Path, dict]:
    text = bytearray(b"\xcc" * 0x160)
    for at, code in CODE.items():
        text[at - 0x1000:at - 0x1000 + len(code)] = code
    data = bytearray(0x20)
    data[0x10:0x18] = (BASE + 0x10A0).to_bytes(4, "little") + (BASE + 0x10B0).to_bytes(4, "little")
    (tmp_path / "orig").mkdir()
    (tmp_path / "orig" / "game.exe").write_bytes(_pe([(".text", 0x1000, bytes(text)), (".data", 0x2000, bytes(data))],
                                                     []))
    relocs = {0x1040: [[8, REL32, "_A@4", 0], [0x12, REL32, "_M", 0]], 0x1100: [[0xB, REL32, "_Add2", 0]]}
    fn = lambda at: {"name": NAMES[at], "addr": BASE + at, "size": len(CODE[at]), "relocs": relocs.get(at, [])}  # noqa: E731
    info = {"exe": "orig/game.exe", "units": {"u": [fn(0x1040), fn(0x1100)], "v": [fn(a) for a in CODE if a not in (0x1040, 0x1100)]}}
    (tmp_path / "config" / "game").mkdir(parents=True)
    (tmp_path / "config" / "game" / "functions.json").write_text(json.dumps(info))
    return tmp_path, {"platform": "win32", "version": "game", "game": {"path": "orig/game.exe"}, "ltcg": ltcg}


def test_functions_called_on_one_global_object_are_member_functions_of_one_struct_with_its_fields(tmp_path):
    root, manifest = _program(tmp_path, ltcg=False)
    prog = convention.load(root, manifest)
    objs = methods.recover(prog, ltcg=False)
    a, m = BASE + 0x1000, BASE + 0x1020
    g = objs.of[a]
    assert objs.of[m] is g and g.keyword == "struct" and g.objects == {OBJ}
    assert g.methods[a] == "ecx"
    assert {o: f.ctype() for o, f in g.fields.items()} == {0: "s32", 4: "f32", 8: "f32", 0xC: "s32"}
    assert BASE + 0x1060 not in objs.of  # a __cdecl function taking a pointer on the stack is a free function
    decl = methods.declaration(prog, g)
    assert decl.startswith(f"struct {g.name} {{")
    assert "    s32 field_00;  // +0x00" in decl and "    f32 field_08;  // +0x08" in decl
    assert "    int A(int arg1);" in decl  # `ret 4`: one stack argument after `this`
    text = "\n".join(methods.brief(prog, objs, a, False))
    assert f"### Looks like a member function of struct {g.name}" in text
    assert "`this` arrives in ecx (__thiscall" in text and "this->+0x0 s32" in text
    assert f"`int {g.name}::A(int arg1)`" in text


def test_a_constructor_storing_a_vtable_makes_a_class_with_its_virtual_functions(tmp_path):
    root, manifest = _program(tmp_path, ltcg=False)
    prog = convention.load(root, manifest)
    objs = methods.recover(prog, ltcg=False)
    g = objs.of[BASE + 0x1080]
    assert g.keyword == "class" and g.vtables[0].addr == VTBL
    assert g.vtables[0].slots == [BASE + 0x10A0, BASE + 0x10B0]
    assert objs.of[BASE + 0x10A0] is g and objs.of[BASE + 0x10B0] is g and g.ctors == {BASE + 0x1080}
    assert 0 not in g.fields and g.fields[8].ctype() == "f32"  # +0 is the vtable pointer
    shown = methods.declaration(prog, g)
    assert "virtual int V1(void);  // slot 0" in shown
    assert objs.find(VTBL) is g


def test_under_ltcg_this_is_found_in_the_register_the_linker_chose(tmp_path):
    root, manifest = _program(tmp_path, ltcg=True)
    prog = convention.load(root, manifest)
    objs = methods.recover(prog, ltcg=True)
    add2 = BASE + 0x10E0
    assert objs.this[add2] == "esi" and objs.of[add2] is objs.of[BASE + 0x1000]  # the same global object
    assert objs.ambiguous[BASE + 0x1120] == ["ecx", "edx"]  # Dot(a, b): two objects alike, neither is `this`
    assert BASE + 0x1140 not in objs.of  # Sum walks its pointer: an array, not an object
    text = "\n".join(methods.brief(prog, objs, add2, True))
    assert "`this` arrives in esi: the linker's choice under link-time code generation" in text
    assert "int edx" in text  # the other register parameter stays a parameter
    assert "### Two objects alike" in "\n".join(methods.brief(prog, objs, BASE + 0x1120, True))


def test_agents_claiming_at_once_never_see_a_convention_still_being_worked_out(tmp_path, monkeypatch):
    # The app claims in worker threads that share load()'s cached program; a claim once read another's recursion
    # guard (None) and failed with "'NoneType' object has no attribute 'describe'".
    import threading

    root, manifest = _program(tmp_path, ltcg=True)
    prog = convention.load(root, manifest)
    slow, real = threading.Event(), convention.analyse

    def analyse(*a):
        slow.wait(0.2)  # long enough for the other threads to arrive mid-analysis
        return real(*a)

    monkeypatch.setattr(convention, "analyse", analyse)
    errors, add2 = [], BASE + 0x10E0

    def claim():
        try:
            convention.evidence(prog, add2)
            methods.recover(prog, True)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=claim) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []

    def broken(*a):
        raise RuntimeError("bad code")

    monkeypatch.setattr(convention, "analyse", broken)
    fresh = convention.index(prog.pe, json.loads((root / "config" / "game" / "functions.json").read_text()))
    for _ in range(2):  # a failed analysis is not remembered as a cycle: the second call fails the same way
        try:
            fresh.convention(add2)
        except RuntimeError:
            pass
        else:
            raise AssertionError("analyse should have failed")
    assert add2 not in fresh.__dict__.get("_conventions", {})


def _engine():
    from decompnet.app.forkengine import ForkEngine

    return ForkEngine.__new__(ForkEngine)


def test_briefs_and_standins_say_which_struct_a_function_belongs_to(tmp_path):
    root, manifest = _program(tmp_path, ltcg=True)
    fp = SimpleNamespace(root=root, manifest=manifest)
    engine = _engine()
    brief = "\n".join(engine._method_brief(fp, {"platform": "xbox"}, {"symbol": "_Add2"}, {"name": "Add2", "meta": None}))
    assert "### Looks like a member function of struct" in brief and "`this` arrives in esi" in brief
    assert "```cpp\nstruct " in brief  # the declaration to start from
    plain = SimpleNamespace(root=root, manifest={**manifest, "ltcg": False})
    assert engine._method_brief(plain, {}, {"symbol": "_Add2"}, {"name": "Add2", "meta": None}) == []  # (ECX-only readers cover it)
    prog = convention.load(root, manifest)
    s = standins.skeleton(prog, "u")
    name = methods.recover(prog, True).of[BASE + 0x1000].name
    assert f"struct {name} {{" in s["structs"] and "int Add2(int edx);" in s["structs"]
    assert f"// looks like a member function of struct {name} (`this` in esi): int {name}::Add2(int edx)" \
        in s["standins"]
    assert "### Structs and classes its functions and callees look like member functions of" \
        in standins.render(prog, "u")
    # a symbol that says the real signature wins over the guess from the registers
    meta = {"symbol": "_Add2", "demangled": "public: virtual void __thiscall C_vt_003A94FC::Update(struct CCamOut *,float)"}
    brief = "\n".join(engine._method_brief(fp, {}, meta, {"name": "Add2", "meta": None}))
    assert "`virtual void C_vt_003A94FC::Update(CCamOut *, float)`" in brief and "Struct_" not in brief


def _delink():
    import importlib.util

    path = Path(methods.__file__).parents[1] / "consoles" / "pc" / "project_template" / "tools" / "delink.py"
    spec = importlib.util.spec_from_file_location("template_delink", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_attempt_whose_function_the_linker_gave_another_convention_is_told_so(tmp_path):
    """The harness-made object holds Add2 reading `this` from ecx where the original reads esi (a driver call or a
    helper changed the linker's choice): the result says which registers differ and what to do about it."""
    root, manifest = _program(tmp_path, ltcg=True)
    fp = SimpleNamespace(root=root, manifest=manifest)
    ours = bytes.fromhex("8b01" "03c2" "8901" "c3")  # this in ecx, k in edx
    obj = tmp_path / "u.obj"
    obj.write_bytes(_delink().coff([{"name": "_Add2", "offset": 0, "size": len(ours), "relocs": [], "addr": BASE}],
                                   ours))
    unit = SimpleNamespace(base=obj)
    text = "\n".join(_engine()._convention_warning(fp, {"symbol": "_Add2"}, {"name": "Add2"}, unit))
    assert "registers read before written: original edx, esi, yours ecx, edx" in text
    assert "keep the original's number of call sites" in text
    same = tmp_path / "same.obj"
    original = bytes.fromhex("8b06" "03c2" "8906" "c3")
    same.write_bytes(_delink().coff([{"name": "_Add2", "offset": 0, "size": 7, "relocs": [], "addr": BASE}], original))
    assert _engine()._convention_warning(fp, {"symbol": "_Add2"}, {"name": "Add2"}, SimpleNamespace(base=same)) == []
    assert _engine()._convention_warning(fp, {"symbol": "_Add2"}, {"name": "Add2"}, SimpleNamespace(base=None)) == []


def test_a_member_functions_known_signature_is_read_from_its_undecorated_name():
    """An Xbox game built with link-time code generation: a brief suggested `int Struct_0015F270::?Update(int arg1, int arg2)` for a function whose symbol
    said `public: virtual void __thiscall C_vt_003A94FC::Update(struct CCamOut *, float)`."""
    assert methods.known_signature("public: virtual void __thiscall C_vt_003A94FC::Update(struct CCamOut *,float)") \
        == ("C_vt_003A94FC", "virtual void C_vt_003A94FC::Update(CCamOut *, float)")
    assert methods.known_signature("public: int __thiscall Accum::Get(void)const") \
        == ("Accum", "int Accum::Get() const")
    assert methods.known_signature("public: __thiscall CFoo::CFoo(class CBar &)") == ("CFoo", "CFoo::CFoo(CBar &)")
    assert methods.known_signature("void __cdecl Free(int)") is None  # not a member function
    assert methods.known_signature(None) is None
    assert methods._c_name("?Update@C_vt_003A94FC@@UAEXPAUCCamOut@@M@Z") == "Update"
    assert methods._c_name("_func_000115A0@8") == "func_000115A0"
