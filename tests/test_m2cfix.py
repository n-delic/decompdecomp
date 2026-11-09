"""m2c's usual mistakes undone (m2cfix.py): the drafts here are made up, small and about nothing in particular."""

import pytest

from decompnet import m2cfix

CALL_ASM = """glabel func_80001000
addiu $sp, $sp, -0x18
sw $ra, 0x10($sp)
jal func_80002000
addiu $a1, $zero, 0x1
lw $ra, 0x10($sp)
jr $ra
addiu $sp, $sp, 0x18
endlabel func_80001000
"""


def test_the_macros_are_written_out_innermost_first():
    src = ("#ifndef M2C_DEFINES\n#define M2C_DEFINES\ntypedef s32 M2C_UNK;\n#endif\n"
           "void f(void *p) { M2C_FIELD(M2C_FIELD(p, void **, 4), s32 *, 8) = 1; }")
    out = m2cfix.expand_macros(src)
    assert "M2C_" not in out
    assert "(*(s32 *)((char *)((*(void **)((char *)(p) + 4))) + 8)) = 1;" in out


@pytest.mark.parametrize('in_header', [False,True])
def test_declared_typed_field_macros_expand_nested_loads_and_stores(in_header):
    define = '#define F(p,t,o) (*(t*)((u8*)(p)+(o)))\n'
    source = 'void f(void *p) { F(F(p,void *,4),u16,8) |= 1; }'
    out = m2cfix.expand_macros(source if in_header else define+source, define if in_header else '')
    assert 'F(F' not in out and '(*(u16 *)' in out and '|= 1;' in out
    assert out[out.index('{'):].count('(p)')==1  # each original argument remains evaluated once


def test_unrelated_field_names_comments_strings_and_directives_are_opaque():
    source = '#define F(p,t,o) (*(t*)((u8*)(p)+(o)))\nvoid f(void *p) { F(p,f32,4)=1; puts("F(p,u8,0)"); /* F(p,u8,0) */ }'
    out = m2cfix.expand_macros(source)
    assert out.startswith(source.splitlines()[0]) and '"F(p,u8,0)"' in out and '/* F(p,u8,0) */' in out
    assert 'F(p,f32,4)' not in out
    unrelated = source.replace('(*(t*)((u8*)(p)+(o)))', 'lookup(p,t,o)')
    assert m2cfix.expand_macros(unrelated)==unrelated
    assert m2cfix.expand_macros('int f(){return F(a,b,c);}')=='int f(){return F(a,b,c);}'


def test_typed_field_expansion_compiles_identically_and_reaches_real_permuter(tmp_path):
    from decompnet import toolchains
    from decompnet.app.permute import Generator
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc') or not toolchains.installed('decomp-permuter'):
        pytest.skip('needs already-installed compiler and permuter')
    header = 'typedef unsigned char u8; typedef unsigned short u16; typedef float f32;\n'
    source = '#define F(p,t,o) (*(t*)((u8*)(p)+(o)))\nf32 f(void *p) { F(p,u16,2) |= 1; return F(p,f32,8)*0.5f; }'
    expanded = m2cfix.expand_macros(source)
    cfg = compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2','-G0','-mips3','-mgp32','-mfp32']}})
    objects = [compile_source(cfg,header,text) for text in (source,expanded)]
    assert all(obj.ok for obj in objects), [obj.log for obj in objects]
    assert extract_function(objects[0].obj,'f').fingerprint()==extract_function(objects[1].obj,'f').fingerprint()
    gen = Generator()
    try:
        assert gen.batch({'header':header,'source':expanded,'function':'f','family':'gcc','seed':7,'count':4})
    finally:
        gen.close()


def test_a_call_passes_on_the_argument_registers_the_code_leaves_alone():
    """The code sets only $a1 for the call: $a0 is the caller's own, passed on, and the function takes it."""
    draft = "s32 func_80002000(s32);\nvoid func_80001000(void) {\n    func_80002000(1);\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", CALL_ASM)
    assert "void func_80001000(s32 arg0)" in out and "func_80002000(arg0, 1);" in out
    assert "func_80002000();" in out  # (its prototype takes whatever the code passes)


def test_nothing_is_added_to_a_call_that_has_its_arguments():
    draft = "void func_80001000(s32 arg0) {\n    func_80002000(arg0, 1);\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", CALL_ASM)
    assert "func_80002000(arg0, 1);" in out and "arg0, arg0" not in out


def test_every_argument_up_to_the_highest_is_declared():
    draft = "s32 func_80001000(s32 arg2) {\n    return arg2;\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\naddu $v0, $a2, $zero\n")
    assert "func_80001000(s32 arg0, s32 arg1, s32 arg2)" in out  # (m2c named only the one it reads)


BYTE_RELOAD_ASM = '''glabel func_80001000
lw $v0, 0x20($a0)
sw $v0, 8($a0)
lbu $v1, 8($a0)
addiu $v0, $zero, 1
bne $v1, $v0, done
nop
done:
jr $ra
nop
'''
BYTE_RELOAD_DRAFT = '''void func_80001000(void *arg0) {
    M2C_FIELD(arg0, s32 *, 8) = M2C_FIELD(arg0, s32 *, 0x20);
    if ((u8) M2C_FIELD(arg0, s32 *, 8) == 1) { func_80002000(); }
}
'''


def test_target_byte_reload_is_not_word_truncation():
    refined = m2cfix.refine_draft(BYTE_RELOAD_DRAFT, 'func_80001000', BYTE_RELOAD_ASM)
    assert 'if (M2C_FIELD(arg0, u8 *, 8) == 1)' in refined
    assert 'M2C_FIELD(arg0, s32 *, 8) = M2C_FIELD(arg0, s32 *, 0x20)' in refined
    # In big-endian storage, the first addressed byte is the high byte, not (u8)word.
    word = 0x01020304
    assert int.from_bytes(word.to_bytes(4, 'big')[:1], 'big') == 1
    assert word & 0xff == 4


@pytest.mark.parametrize('asm', [
    '', BYTE_RELOAD_ASM.replace('lbu $v1', 'lw $v1'),
    BYTE_RELOAD_ASM.replace('lbu $v1', 'lb $v1'),
    BYTE_RELOAD_ASM.replace('lbu $v1, 8', 'lbu $v1, 9'),
    BYTE_RELOAD_ASM.replace('lbu $v1, 8($a0)', 'lbu $v1, 8($a1)'),
    'addiu $a0, $a0, 4\n'+BYTE_RELOAD_ASM,
    'jal func_80003000\nnop\n'+BYTE_RELOAD_ASM,
    'bnez $v0, done\nnop\n'+BYTE_RELOAD_ASM,
])
def test_byte_reload_requires_direct_unchanged_argument_address(asm):
    assert '(u8) M2C_FIELD(arg0, s32 *, 8)' in m2cfix.refine_draft(BYTE_RELOAD_DRAFT, 'func_80001000', asm)


def test_byte_reload_changes_only_selected_function_and_real_expressions():
    source = BYTE_RELOAD_DRAFT.replace('    if', '    /* (u8) M2C_FIELD(arg0, s32 *, 8) */\n    if')
    other = BYTE_RELOAD_DRAFT.replace('func_80001000', 'func_80004000')
    refined = m2cfix._byte_field_loads(source+other, 'func_80001000', BYTE_RELOAD_ASM)
    assert refined.endswith(other)
    assert '/* (u8) M2C_FIELD(arg0, s32 *, 8) */' in refined
    assert m2cfix._byte_field_loads(source, 'missing', BYTE_RELOAD_ASM) == source
    custom = '#define M2C_FIELD(p,t,o) other(p,t,o)\n'+source
    assert m2cfix._byte_field_loads(custom, 'func_80001000', BYTE_RELOAD_ASM) == custom
    reassigned = source.replace('    if', '    arg0 += 4;\n    if')
    assert m2cfix._byte_field_loads(reassigned, 'func_80001000', BYTE_RELOAD_ASM) == reassigned


@pytest.mark.parametrize('source', [
    BYTE_RELOAD_DRAFT.replace('    if', '    arg0 <<= 1;\n    if'),
    BYTE_RELOAD_DRAFT.replace('    if', '    change(&arg0);\n    if'),
    BYTE_RELOAD_DRAFT.replace('    if', '    change(&(arg0));\n    if'),
    BYTE_RELOAD_DRAFT.replace('    if', '    unknown();\n    if'),
    BYTE_RELOAD_DRAFT.replace('void *arg0', 's32 arg0'),
    BYTE_RELOAD_DRAFT.replace(' == 1', ' == (u8) M2C_FIELD(arg0, s32 *, 8)'),
    BYTE_RELOAD_DRAFT.replace(' == 1', ' == (u8) M2C_FIELD(arg0, s32 *, 0x8)'),
])
def test_byte_reload_rejects_ambiguous_source_binding_or_multiple_reads(source):
    assert m2cfix._byte_field_loads(source, 'func_80001000', BYTE_RELOAD_ASM) == source


def test_byte_reload_compiles_like_explicit_byte_control(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs already-installed compiler')
    header = ('typedef int s32; typedef unsigned char u8;\n'
              '#define M2C_FIELD(p,t,o) (*(t)((char *)(p)+(o)))\n')
    refined = m2cfix.refine_draft(BYTE_RELOAD_DRAFT, 'func_80001000', BYTE_RELOAD_ASM)
    control = BYTE_RELOAD_DRAFT.replace('(u8) M2C_FIELD(arg0, s32 *, 8)', 'M2C_FIELD(arg0, u8 *, 8)')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                           'flags': ['-O2', '-G0', '-mips3', '-mgp32', '-mfp32']}})
    results = [compile_source(cfg, header, s) for s in (BYTE_RELOAD_DRAFT, refined, control)]
    assert all(r.ok for r in results), [r.log for r in results]
    functions = [extract_function(r.obj, 'func_80001000') for r in results]
    assert functions[1].fingerprint() == functions[2].fingerprint()
    assert functions[0].fingerprint() != functions[2].fingerprint()


def test_saved_draft_search_exposes_byte_reload_as_m2c_alternative():
    from decompnet.app import draft_repairs
    labels = {}
    candidates = draft_repairs.variants(BYTE_RELOAD_DRAFT, 'func_80001000', 'mips', BYTE_RELOAD_ASM, labels=labels)
    assert any(labels.get(candidate) == 'm2c_refinement' and
               'if (M2C_FIELD(arg0, u8 *, 8) == 1)' in candidate for candidate in candidates)
    draft = "void func_80001000(void) {\n    func_80002000(1);\n}\n"
    asm = "glabel f\njal func_80002000\naddiu $a2, $zero, 1\n"
    assert "func_80001000(s32 arg0, s32 arg1)" in m2cfix.refine_draft(draft, "func_80001000", asm)


def test_a_global_at_an_offset_is_a_variable_of_its_own():
    draft = "extern M2C_UNK D_80100000;\nvoid func_80001000(void) {\n    M2C_FIELD(&D_80100000, s16 *, 0x10) = 3;\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\nnop\n")
    assert "extern short D_80100010;" in out and "D_80100010 = 3;" in out


def test_colour_bytes_are_stored_through_unsigned_fields():
    draft = "void func_80001000(void *arg0) {\n    M2C_FIELD(arg0, s8 *, 0x90) = 0xFE;\n    M2C_FIELD(arg0, s8 *, 0x91) = 0x1E;\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\nnop\n")
    assert "M2C_FIELD(arg0, u8 *, 0x90) = 0xFE;" in out and "M2C_FIELD(arg0, s8 *, 0x91) = 0x1E;" in out


def test_x_equals_x_or_c_is_a_compound_assignment():
    draft = "void func_80001000(void *arg0) {\n    M2C_FIELD(arg0, s32 *, 0x80) = (s32) (M2C_FIELD(arg0, s32 *, 0x80) | 4);\n}\n"
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\nnop\n")
    assert "M2C_FIELD(arg0, s32 *, 0x80) |= 4;" in out


def test_a_pointer_stepped_by_a_byte_count_is_a_byte_pointer():
    draft = ("void func_80001000(s16 *arg0) {\n    s16 *var_a0;\n\n    var_a0 = arg0;\n    *var_a0 = 0;\n"
             "    var_a0 += 0x24;\n}\n")
    out = m2cfix.refine_draft(draft, "func_80001000", "glabel func_80001000\njr $ra\nnop\n")
    assert "char *var_a0;" in out and "*(s16 *)var_a0 = 0;" in out and "var_a0 += 0x24;" in out


def test_a_header_it_cant_read_gives_no_refined_draft():
    assert m2cfix.refine_draft("void other(void) {\n}\n", "func_80001000", "glabel f\njr $ra\nnop\n") is None


def test_the_permuters_inline_helper_is_static():
    src = "inline char *inline_fn(void *a)\n{\n  return (char *) a;\n}\n\nvoid f(void *a) { inline_fn(a); }\n"
    out = m2cfix.static_helpers(src)
    assert out.startswith("static inline char *inline_fn") and m2cfix.static_helpers(out) == out


def test_overlay_aliases_use_explicit_target_not_address_guess():
    from decompnet.m2cfix import overlay_call_aliases
    source='void f(void) { func_80100000_ovl_12(); /* func_80100000_ovl_12 */ }'
    changed=overlay_call_aliases(source,'jal func_80100000_overlay_027')
    assert 'func_80100000_overlay_027();' in changed
    assert '/* func_80100000_ovl_12 */' in changed
    assert overlay_call_aliases(source,'jal func_80100000_overlay_027\njal func_80100000_overlay_028')==source


def test_inline_helpers_preserve_existing_storage_order_and_line_breaks():
    for prefix in ("static inline", "inline static", "inline /* type */ static",
                   "inline /* storage */ static", "inline\nstatic"):
        source = prefix + " int helper(void) { return 1; }\n"
        assert m2cfix.static_helpers(source) == source


def test_inline_helpers_ignore_comments_strings_and_body_storage():
    source = ('/*\ninline int fake(void);\n*/\n'
              'const char *text = "inline int fake(void);";\n'
              '#define HELPER inline int fake(void)\n'
              '\tinline /* static ( */ int helper(void) { static int x; return x; }\n')
    expected = source.replace('\tinline', '\tstatic inline')
    assert m2cfix.static_helpers(source) == expected
    assert m2cfix.static_helpers(expected) == expected


# Drafts that did not compile on a 636-function N64 game (122 of them had C): each test below is one cause, small.
_NOP_ASM = "glabel f\njr $ra\nnop\nendlabel f\n"


def test_unknown_types_become_whole_type_names_never_a_mashup():
    out = m2cfix.refine_draft("M2C_UNK32 g;\nM2C_UNK8 h;\nM2C_UNK16 i;\nM2C_UNK j;\nvoid f(void) {\n}\n", "f", _NOP_ASM)
    assert "s32 g;" in out and "s8 h;" in out and "s16 i;" in out and "s32 j;" in out
    assert "s3232" not in out and "s328" not in out


def test_every_prototype_m2c_guessed_loses_its_arguments_not_only_the_games_functions():
    draft = ("s32 osVirtualToPhysical(u32, u32, s32);             /* extern */\n"
             "void *real(void *);\n"
             "void f(void) {\n    osVirtualToPhysical(1, 2, 3);\n    osVirtualToPhysical(4);\n}\n")
    out = m2cfix.refine_draft(draft, "f", _NOP_ASM)
    assert "s32 osVirtualToPhysical();" in out
    assert "void *real(void *);" in out  # a prototype m2c did not guess (the header's) stays


def test_the_address_of_an_array_global_is_the_array_so_arithmetic_on_it_compiles():
    draft = ("extern M2C_UNK D_80001000;\nvoid f(s32 arg0) {\n"
             "    s32 a;\n    a = (arg0 * 4) + &D_80001000;\n    a = a & D_80001000;\n}\n")
    out = m2cfix.refine_draft(draft, "f", _NOP_ASM)
    assert "extern char D_80001000[];" in out
    assert "(arg0 * 4) + D_80001000;" in out
    assert "a & D_80001000;" in out  # a binary & is not an address


def test_a_global_the_draft_retypes_is_declared_once():
    draft = ("extern M2C_UNK D_80001000;\nvoid f(void *arg0) {\n"
             "    M2C_FIELD(&D_80001000, f32 *, 0) = 1.0f;\n}\n")
    out = m2cfix.refine_draft(draft, "f", _NOP_ASM)
    assert out.count("D_80001000;") + out.count("D_80001000[];") == 1


def test_a_function_calling_itself_declares_what_it_passes_and_completes_shorter_calls():
    draft = "void f(s32 arg0) {\n    f(arg0, 1, 2);\n    f(arg0);\n}\n"
    out = m2cfix.refine_draft(draft, "f", _NOP_ASM)
    assert "void f(s32 arg0, s32 arg1, s32 arg2) {" in out
    assert "f(arg0, 1, 2);" in out and "f(arg0, arg1, arg2);" in out


def test_a_callee_saved_register_read_before_it_is_set_is_left_unset():
    from decompnet import seeds
    source = "s8 f(void) {\n    s8 var_s0;\n\n    var_s0 = saved_reg_s0;\n    if (g()) {\n        var_s0 = 1;\n    }\n    return var_s0;\n}\n"
    out = seeds.drop_saved_register_reads(source)
    assert "saved_reg_" not in out and "var_s0 = 1;" in out and "return var_s0;" in out


def test_a_jump_table_nobody_imported_says_what_is_missing():
    import subprocess
    from decompnet import seeds
    done = subprocess.CompletedProcess([], 1, "/* Decompilation failure in function f:\n\nFound jr instruction at f.s line 8, "
                                       "but the corresponding jump table is not provided.  Please include it. */", "")
    reason = seeds._failure(done)
    assert "no bytes at the table's address" in reason and reason.startswith("Decompilation failure")
