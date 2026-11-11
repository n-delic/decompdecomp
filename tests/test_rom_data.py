"""Data a submission defines itself (strings, float constants, tables) is judged by what the game has where the
code points, whatever the order the code uses it in and however the game's linker laid it out.

The game here is linked from hand-written assembly with the very instructions KMC GCC makes for `b`, but its
data the way IDO-era games keep it: the string shared with an earlier function first, then the next function's
string right after "hey" (no padding), constants at the end. Compiled alone, the submission has "hey" padded,
then "hello", then the double. Every one of those must be found equal; a wrong one must not.
"""

import shutil
import subprocess
from io import BytesIO

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.compiler import CompilerConfig, compile_source
from decompnet.rom import match
from decompnet.rom.symbols import Symbol, SymbolTable

pytestmark = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None or not shutil.which("ld.lld"),
                                reason="needs gcc-2.7.2-kmc and ld.lld")

GAME = """
    .set noreorder
    .text
    .globl show
show:
    jr $31
    nop
    .globl showd
showd:
    jr $31
    nop
    .globl a
a:
    addiu $sp,$sp,-24
    lui $4,%hi(s_hello)
    addiu $4,$4,%lo(s_hello)
    sw $31,16($sp)
    jal show
    nop
    lw $31,16($sp)
    jr $31
    addiu $sp,$sp,24
    .globl b
b:
    addiu $sp,$sp,-24
    lui $4,%hi(s_hey)
    addiu $4,$4,%lo(s_hey)
    sw $31,16($sp)
    jal show
    nop
    lui $1,%hi(k_2_5)
    jal showd
    ldc1 $f12,%lo(k_2_5)($1)
    lui $4,%hi(s_hello)
    jal show
    addiu $4,$4,%lo(s_hello)
    lw $31,16($sp)
    jr $31
    addiu $sp,$sp,24
    nop
    .globl c
c:
    lui $4,%hi(s_next)
    j show
    addiu $4,$4,%lo(s_next)

    .section .rodata
s_hello: .asciz "hello"
    .align 3
s_hey: .asciz "hey"
s_next: .asciz "next"
    .align 3
k_2_5: .double 2.5
"""

SOURCE = """extern void show(const char *);
extern void showd(double);
void b(void) { show("%s"); showd(%s); show("%s"); }
"""


@pytest.fixture(scope="module")
def game(tmp_path_factory):
    work = tmp_path_factory.mktemp("game")
    tc = toolchains.get("gcc-2.7.2-kmc")
    tc_dir = str(toolchains.require(tc.id))
    (work / "game.s").write_text(GAME)
    subprocess.run([f"{tc_dir}/as", "-mips3", "-G0", "-EB", "-o", str(work / "game.o"), str(work / "game.s")],
                   check=True)
    (work / "game.ld").write_text("SECTIONS { . = 0x80000400; .text : { *(.text) } .rodata : { *(.rodata) } }")
    subprocess.run(["ld.lld", "-m", "elf32btsmip", "-T", str(work / "game.ld"), "-o", str(work / "game.elf"),
                    str(work / "game.o")], check=True)
    elf = ELFFile(BytesIO((work / "game.elf").read_bytes()))
    syms = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols() if s.name}
    image = {}
    for name in (".text", ".rodata"):
        sec = elf.get_section_by_name(name)
        image[sec["sh_addr"]] = sec.data()

    def read(addr, n):
        for base, data in image.items():
            if base <= addr and addr + n <= base + len(data):
                return data[addr - base:addr - base + n]
        return None

    text = elf.get_section_by_name(".text")
    code = read(syms["b"], 0x3C)  # without the alignment nop after it, like the compiled function
    hi, lo, r26 = match.R_MIPS_HI16, match.R_MIPS_LO16, match.R_MIPS_26
    relocs = [(0x04, hi, syms["s_hey"]), (0x08, lo, syms["s_hey"]), (0x10, r26, syms["show"]),
              (0x18, hi, syms["k_2_5"]), (0x1C, r26, syms["showd"]), (0x20, lo, syms["k_2_5"]),
              (0x24, hi, syms["s_hello"]), (0x28, r26, syms["show"]), (0x2C, lo, syms["s_hello"])]
    assert text["sh_addr"] <= syms["b"]
    return syms, read, match.RomTarget("b", syms["b"], code, relocs)


def tables(syms):
    """What a project knows: only the functions' names, or every label splat gives the data too."""
    funcs = [Symbol(n, syms[n], 0, "func") for n in ("show", "showd", "a", "b", "c")]
    labels = [Symbol(f"D_{syms[n]:08X}", syms[n], 0, "data") for n in ("s_hello", "s_hey", "s_next", "k_2_5")]
    return {"functions only": SymbolTable(funcs), "splat labels": SymbolTable(funcs + labels)}


def judge(game, hey="hey", k="2.5", hello="hello"):
    syms, read, target = game
    tc = toolchains.get("gcc-2.7.2-kmc")
    tc_dir = str(toolchains.require(tc.id))
    cmd = [a.replace("{tc}", tc_dir) for a in tc.cc] + tc.flags + ["-I{include}", "{src}", "-o", "{obj}"]
    res = compile_source(CompilerConfig(cmd=cmd, ro_paths=(tc_dir,)), "", SOURCE % (hey, k, hello))
    assert res.ok, res.log
    return {name: match.link(res.obj, "b", target, table, read) for name, table in tables(syms).items()}


def test_own_data_in_another_layout_matches(game):
    for name, linked in judge(game).items():
        assert linked.exact, (name, linked.problems)


@pytest.mark.parametrize("change", [{"hey": "hez"}, {"hello": "hellp"}, {"k": "2.25"}, {"hey": "he"},
                                    {"hello": "hell"}])
def test_wrong_data_is_caught(game, change):
    for name, linked in judge(game, **change).items():
        assert not linked.exact and linked.problems, (name, change)


@pytest.mark.skipif(toolchains.installed("ee-gcc-2.96") is None, reason="needs ee-gcc-2.96 for $gp-relative code")
def test_gp_rel_in_a_defined_region_is_relocated(tmp_path):
    """A region the candidate defines (a static clone of a matched callee) can hold a $gp-relative instruction.
    verify_data must apply that GPREL16 relocation (immediate = address - $gp) before comparing with the ROM;
    otherwise the clone's gp-relative half-word compares as the assembler's unrelocated 0 and the match is lost
    at score 1 though every byte that matters is right (setup issue #494)."""
    from elftools.elf.relocation import RelocationSection
    d = str(toolchains.require("ee-gcc-2.96")); cc = f"{d}/bin/ee-gcc"
    TEXT, FCLONE, G, GP = 0x80010000, 0x80010100, 0x80017F00, 0x80018000
    named = {"caller": TEXT, "clone": FCLONE, "g": G}
    sec_base = {".text": TEXT, ".fclone": FCLONE}
    (tmp_path / "c.c").write_text(
        "extern int g;\n"
        "int clone(void) __attribute__((section(\".fclone\")));\n"
        "int clone(void){ return g; }\n"
        "int caller(void){ return clone(); }\n")
    subprocess.run([cc, "-c", "-O2", "-G8", "-o", str(tmp_path / "cand.o"), str(tmp_path / "c.c")], check=True)
    cobj = (tmp_path / "cand.o").read_bytes()
    ce = ELFFile(BytesIO(cobj)); symtab = ce.get_section_by_name(".symtab")

    def resolve(sym):  # a symbol -> its ROM address (named thing, or a section + offset)
        if sym.name in named:
            return named[sym.name]
        sh = sym["st_shndx"]
        if isinstance(sh, int) and ce.get_section(sh).name in sec_base:
            return sec_base[ce.get_section(sh).name] + sym["st_value"]
        return None

    def relocs_of(name):  # (offset, type, target ROM address) of the relocations inside one section
        idx = next(i for i, s in enumerate(ce.iter_sections()) if s.name == name)
        out = []
        for s in ce.iter_sections():
            if isinstance(s, RelocationSection) and s["sh_info"] == idx:
                for r in s.iter_relocations():
                    a = resolve(symtab.get_symbol(r["r_info_sym"]))
                    if a is not None:
                        out.append((r["r_offset"], r["r_info_type"], a))
        return out

    def apply(name):  # raw MIPS ABI relocation, an oracle independent of match.py
        data = bytearray(ce.get_section_by_name(name).data())
        for off, typ, a in relocs_of(name):
            word = int.from_bytes(data[off:off + 4], "little")
            if typ == 2:    word = a
            elif typ == 4:  word = (word & ~0x03FFFFFF) | (((a + ((word & 0x03FFFFFF) << 2)) >> 2) & 0x03FFFFFF)
            elif typ == 7:  word = (word & ~0xFFFF) | ((a - GP) & 0xFFFF)
            elif typ == 5:  word = (word & ~0xFFFF) | (((a + 0x8000) >> 16) & 0xFFFF)
            elif typ == 6:  word = (word & ~0xFFFF) | (a & 0xFFFF)
            data[off:off + 4] = word.to_bytes(4, "little")
        return bytes(data)

    assert any(t == match.R_MIPS_GPREL16 for _, t, _ in relocs_of(".fclone")), "fixture lost its GPREL16 reloc"
    image = {TEXT: apply(".text"), FCLONE: apply(".fclone"), G: (0x12345678).to_bytes(4, "little")}

    def read(a, n):
        for base, data in image.items():
            if base <= a and a + n <= base + len(data):
                return data[a - base:a - base + n]
        return None

    target = match.RomTarget("caller", TEXT, image[TEXT], relocs_of(".text"), big_endian=False, gp=GP)
    table = SymbolTable([Symbol("caller", TEXT, 0, "func"), Symbol("clone", FCLONE, 0, "func"),
                         Symbol("g", G, 4, "data")])
    res = match.link(cobj, "caller", target, table, read)
    assert res.exact, res.problems

_EE = pytest.mark.skipif(toolchains.installed("ee-gcc-2.96") is None, reason="needs ee-gcc-2.96")


def _defined_region_oracle(tmp_path, src, named_ext=(), gp=0x80018000, base=0x80010000):
    """Compile `src` (its entry point is `caller`, at .text+0) with ee-gcc and build an oracle ROM for it.

    Returns (obj, RomTarget, SymbolTable, read). `named_ext` is (name, addr, bytes, kind) for symbols the game
    provides. A section relocation's target folds in the REL field's addend, the way a disassembler records an
    absolute address; `apply` then sets each field from that address, so the oracle is independent of match.py.
    """
    from elftools.elf.relocation import RelocationSection
    d = str(toolchains.require("ee-gcc-2.96")); cc = f"{d}/bin/ee-gcc"
    (tmp_path / "c.c").write_text(src)
    subprocess.run([cc, "-c", "-O2", "-G8", "-o", str(tmp_path / "c.o"), str(tmp_path / "c.c")], check=True)
    obj = (tmp_path / "c.o").read_bytes()
    e = ELFFile(BytesIO(obj)); st = e.get_section_by_name(".symtab")
    prog = [s.name for s in e.iter_sections()
            if s.name and s["sh_type"] in ("SHT_PROGBITS", "SHT_NOBITS") and s.name != ".mdebug.eabi64"]
    sec_base = {nm: base + 0x2000 * i for i, nm in enumerate(prog)}
    ext = {n: (a, b, k) for n, a, b, k in named_ext}

    def resolve(sym):
        if sym.name in ext:
            return ext[sym.name][0]
        sh = sym["st_shndx"]
        if isinstance(sh, int) and e.get_section(sh).name in sec_base:
            return sec_base[e.get_section(sh).name] + sym["st_value"]
        return None

    def relocs_of(sec):
        idx = next(i for i, s in enumerate(e.iter_sections()) if s.name == sec)
        data = e.get_section_by_name(sec).data(); out = []
        for s in e.iter_sections():
            if isinstance(s, RelocationSection) and s["sh_info"] == idx:
                for r in s.iter_relocations():
                    a = resolve(st.get_symbol(r["r_info_sym"]))
                    if a is None:
                        continue
                    off, typ = r["r_offset"], r["r_info_type"]
                    w = int.from_bytes(data[off:off + 4], "little")
                    if typ == match.R_MIPS_26:
                        a += (w & 0x03FFFFFF) << 2
                    elif typ == match.R_MIPS_32:
                        a += w
                    out.append((off, typ, a))
        return out

    def apply(sec):
        data = bytearray(e.get_section_by_name(sec).data())
        for off, typ, a in relocs_of(sec):
            w = int.from_bytes(data[off:off + 4], "little")
            if typ == match.R_MIPS_32:
                w = a
            elif typ == match.R_MIPS_26:
                w = (w & ~0x03FFFFFF) | ((a >> 2) & 0x03FFFFFF)
            elif typ == match.R_MIPS_GPREL16:
                w = (w & ~0xFFFF) | ((a - gp) & 0xFFFF)
            elif typ == match.R_MIPS_HI16:
                w = (w & ~0xFFFF) | (((a + 0x8000) >> 16) & 0xFFFF)
            elif typ == match.R_MIPS_LO16:
                w = (w & ~0xFFFF) | (a & 0xFFFF)
            data[off:off + 4] = w.to_bytes(4, "little")
        return bytes(data)

    image = {sec_base[nm]: apply(nm) for nm in prog if e.get_section_by_name(nm)["sh_type"] != "SHT_NOBITS"}
    for _n, (a, b, _k) in ext.items():
        image[a] = b

    def read(a, n):
        for b, data in image.items():
            if b <= a and a + n <= b + len(data):
                return data[a - b:a - b + n]
        return None

    cal = next(s for s in st.iter_symbols() if s.name == "caller")
    assert cal["st_value"] == 0, "the oracle expects `caller` at .text+0"
    code = image[sec_base[".text"]][:cal["st_size"]]
    rel = [(off, typ, a) for off, typ, a in relocs_of(".text") if off < cal["st_size"]]
    syms = [Symbol("caller", sec_base[".text"], cal["st_size"], "func")]
    for n, (a, b, k) in ext.items():
        syms.append(Symbol(n, a, len(b), k))
    target = match.RomTarget("caller", sec_base[".text"], code, rel, big_endian=False, gp=gp)
    return obj, target, SymbolTable(syms), read


@_EE
def test_r26_call_to_a_static_helper_in_a_defined_region_is_relocated(tmp_path):
    """A matched caller can keep a static helper the game doesn't name in its own section and reach it with `jal`.
    verify_data must relocate that R_MIPS_26 call, and place_pointees must find the helper from the linked ROM
    word, or the call compares as the assembler's unrelocated 0 and the match is lost though every byte is right."""
    src = ("static int inner(void) __attribute__((noinline, section(\".fclone\")));\n"
           "static int outer(void) __attribute__((noinline, section(\".fclone\")));\n"
           "static int outer(void){ return inner() + 1; }\n"
           "static int inner(void){ return 0x42; }\n"
           "int caller(void){ return outer(); }\n")
    obj, target, table, read = _defined_region_oracle(tmp_path, src)
    res = match.link(obj, "caller", target, table, read)
    assert res.exact, res.problems


@_EE
def test_gp_rel_from_a_defined_region_to_a_static_datum_is_placed(tmp_path):
    """A static clone can load a static datum of its own $gp-relatively. The datum has no name the game knows, so
    place_pointees must put it where the ROM's instruction points and verify_data must relocate the load against
    that placement, rather than refuse the nameless symbol (`` is not a known symbol`)."""
    src = ("static int s = 0x12345678;\n"
           "static int helper(void) __attribute__((noinline, section(\".fclone\")));\n"
           "static int helper(void){ return s; }\n"
           "int caller(void){ return helper(); }\n")
    obj, target, table, read = _defined_region_oracle(tmp_path, src)
    res = match.link(obj, "caller", target, table, read)
    assert res.exact, res.problems


@_EE
def test_defined_region_gp_rel_is_refused_without_g0(tmp_path):
    """On a game with no $gp (gp == 0), a $gp-relative reference inside a defined region is refused with the same
    'compile with -G0' message the function's own code gets, not silently accepted."""
    src = ("static int s = 0x12345678;\n"
           "static int helper(void) __attribute__((noinline, section(\".fclone\")));\n"
           "static int helper(void){ return s; }\n"
           "int caller(void){ return helper(); }\n")
    obj, target, table, read = _defined_region_oracle(tmp_path, src, gp=0)
    res = match.link(obj, "caller", target, table, read)
    assert not res.exact
    assert any("compile with -G0" in p for p in res.problems), res.problems


@_EE
def test_static_helper_in_the_functions_own_text_is_out_of_scope(tmp_path):
    """Scope limit: this change covers helpers and data the candidate keeps in its OTHER sections, not a static
    helper that shares the function's own .text. Such a helper the game doesn't name is refused, never silently
    matched with its body unchecked; verifying same-.text helper bodies is left to a follow-up (review point 3)."""
    src = ("int caller(int x);\n"
           "static int helper(int x) __attribute__((noinline));\n"
           "int caller(int x){ return helper(x) + 1; }\n"
           "static int helper(int x){ return x * 3; }\n")
    obj, target, table, read = _defined_region_oracle(tmp_path, src)
    res = match.link(obj, "caller", target, table, read)
    assert not res.exact
    assert any("not a known symbol" in p for p in res.problems), res.problems
