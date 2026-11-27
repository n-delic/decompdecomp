from decompnet import seeds
from decompnet.app import callee_prototypes

HEADER = 'typedef unsigned char u8;\ntypedef float f32;\ntypedef int s32;\n'


def test_a_matched_definition_gives_its_prototype():
    source = 'typedef struct { u8 b; } Def;\ns32 spawn(u8 kind, Def *def, void (*unused)) {\n    return kind;\n}\n'
    assert callee_prototypes.prototype(source, 'spawn', HEADER) is None  # a function pointer is left out
    source = ('typedef struct { u8 b; } Def;\n'
              's32 spawn(u8 kind, Def *def, const char *label, f32 x, f32 m[4]) {\n    return def->b;\n}\n')
    line, types = callee_prototypes.prototype(source, 'spawn', HEADER)
    assert line == 's32 spawn(u8, void *, char *, f32, f32 *);'
    assert types == {'s32', 'u8', 'f32'}


def test_a_type_only_the_callee_declares_is_not_passed_by_value():
    assert callee_prototypes.prototype('void f(Vec v) {\n}\n', 'f', HEADER) is None
    assert callee_prototypes.prototype('static void f(s32 v) {\n}\n', 'f', HEADER) is None
    assert callee_prototypes.prototype('void f() {\n}\n', 'f', HEADER) is None
    assert callee_prototypes.prototype('void f(void) {\n    D = 1;\n}\n', 'f', HEADER) == ('void f(void);', set())


def test_an_empty_function_does_not_say_it_takes_nothing():
    # a debug print compiled out: its callers still load arguments, which a (void) prototype would drop from drafts
    for body in ('{\n}', '{\n    /* compiled out */\n}', '{\n    return;\n}'):
        assert callee_prototypes.prototype(f'void f(void) {body}\n', 'f', HEADER) is None
    # parameters its source does name are still evidence
    assert callee_prototypes.prototype('void f(s32 a) {\n}\n', 'f', HEADER) == ('void f(s32);', {'s32'})


def test_only_matched_callees_the_header_does_not_declare():
    class Store:
        def all(self, query, project, *names):
            assert names == ('spawn',)
            return [{'name': 'spawn', 'matched_source': 'void spawn(f32 x) {\n}\n'}]

    class Net:
        def project_store(self, project):
            return Store()

    asm = 'jal        spawn\nnop\njal        known\njal        outer\n'
    lines, types = callee_prototypes.of_callees(Net(), {'id': 1}, {'name': 'outer'}, asm,
                                                HEADER + 'void known(s32);\n')
    assert lines == ['void spawn(f32); ' + seeds.CALLEE_MARK]
    assert types == {'f32'}


def test_the_draft_declares_the_prototypes_it_was_given(monkeypatch):
    monkeypatch.setattr(seeds, '_generate_from_asm', lambda *args, **kwargs: 'void outer(void) {\n    spawn(1.0f);\n}\n')
    context = 'typedef float f32;\nvoid spawn(f32); ' + seeds.CALLEE_MARK
    draft = seeds.generate_from_asm('', 'outer', 'gcc-2.7.2-kmc', context, valid=True)
    assert draft.startswith('void spawn(f32); ' + seeds.CALLEE_MARK + '\n')
