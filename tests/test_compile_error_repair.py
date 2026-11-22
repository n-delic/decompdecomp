"""A draft the compiler refuses is repaired from its own diagnostics, a bounded number of rounds."""
from types import SimpleNamespace

from decompnet.app import compile_error_repair


def compiler(fails):
    """A compiler that rejects any text still holding one of `fails`, reporting the first failing line."""
    calls = []

    def compile_source(text):
        calls.append(text)
        for number, line in enumerate(text.split('\n'), 1):
            for bad, message in fails.items():
                if bad in line:
                    return SimpleNamespace(ok=False, log=f'func.c:{number}: {message}\n')
        return SimpleNamespace(ok=True, log='')
    return compile_source, calls


def test_the_operator_the_compiler_names_is_repaired_until_it_compiles():
    source = 'void f(void) {\n    x = a + b + c;\n}\n'
    compile_source, calls = compiler({'a + b': 'invalid operands to binary +'})
    found = list(compile_error_repair.candidates(compile_source, source))
    assert found == [('compile_error_repair', 'void f(void) {\n    x = (s32)a + (s32)b + c;\n}\n')]
    assert len(calls) == 2


def test_a_draft_that_already_compiles_is_not_offered_again():
    compile_source, _ = compiler({})
    assert list(compile_error_repair.candidates(compile_source, 'void f(void) {}\n')) == []


def test_an_error_no_repair_fits_stops_without_burning_every_round():
    compile_source, calls = compiler({'x': 'parse error before `x\''})
    assert list(compile_error_repair.candidates(compile_source, 'int x;\n')) == []
    assert len(calls) == 1


def test_the_rounds_are_bounded():
    source = 'void f(void) {\n    x = a + b;\n}\n'
    compile_source, calls = compiler({'x': 'invalid operands to binary +'})
    assert list(compile_error_repair.candidates(compile_source, source, rounds=3)) == []
    assert len(calls) <= 3


def test_a_draft_over_the_compilers_size_limit_is_compacted():
    big = 'void f(void) {\n    x = 1;   /* a comment */\n}\n'

    def compile_source(text):
        if '    ' in text or '/*' in text:
            return SimpleNamespace(ok=False, log='rejected: source too large (max 65536 bytes)')
        return SimpleNamespace(ok=True, log='')

    assert list(compile_error_repair.candidates(compile_source, big)) == [('compile_error_repair', 'void f(void) {\nx = 1;\n}\n')]
