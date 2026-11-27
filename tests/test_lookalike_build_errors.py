"""A look-alike's transfer that doesn't build says why, and two of the donor's objects renamed to one symbol still
declare it once (app/twins.py)."""
import pytest

from decompnet import toolchains
from decompnet.app.twins import Twins, apply
from decompnet.compiler import compile_source
from decompnet.project import compiler_config

DONOR = '''typedef struct { int x; int y; } Obj;
extern Obj D_80100018[];
extern unsigned char D_80100000[];
extern int D_80200000;
int f(int i) {
    unsigned char *p = D_80100000 + i * 100;
    return *(int *) (p + 0x18) + D_80100018[i].y + D_80200000;
}
'''


def test_two_objects_renamed_to_one_declare_it_once():
    # the donor's record base is, in this function, the symbol the donor already uses for the record's field
    out = apply(DONOR, {'D_80100000': 'D_80100018', 'f': 'g'})
    assert out.count('D_80100018[];') == 1
    assert '((unsigned char *) D_80100018) + i * 100' in out
    assert 'extern int D_80200000;' in out


def test_a_scalar_renamed_onto_another_type_is_read_through_its_address():
    source = 'extern short D_1;\nextern int D_2;\nint f(void) { return D_1 + D_2; }\n'
    out = apply(source, {'D_1': 'D_3', 'D_2': 'D_3'})
    assert 'extern short D_3;' in out and out.count('extern') == 1
    assert '(*(int *) &D_3)' in out


def test_alike_declarations_are_left_to_c():
    source = 'extern int D_1;\nextern int D_2;\nint f(void) { return D_1 + D_2; }\n'
    assert apply(source, {'D_1': 'D_2'}) == 'extern int D_2;\nextern int D_2;\nint f(void) { return D_2 + D_2; }\n'


@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None, reason='needs the pinned KMC compiler')
def test_the_merged_source_builds(tmp_path):
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': [
        '-G0', '-mips3', '-mgp32', '-mfp32', '-O1']}})
    out = compile_source(cfg, '', apply(DONOR, {'D_80100000': 'D_80100018'}))
    assert out.ok, out.log


@pytest.mark.parametrize('log, line', [
    ("GNU C version 2.7.2\nfunc.c:13: conflicting types for `D_801E9010'\nfunc.c:10: previous declaration of `D_801E9010'\n",
     "func.c:13: conflicting types for `D_801E9010'"),
    ("func.c: In function `f':\nfunc.c:9: warning: unused variable\nfunc.c:35: request for member `x' in something not a structure or union\n",
     "func.c:35: request for member `x' in something not a structure or union"),
])
def test_the_compilers_first_error_is_the_reason(log, line):
    assert Twins._why({'score': None, 'text': log}) == line
