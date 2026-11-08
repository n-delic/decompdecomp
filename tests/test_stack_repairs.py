"""Storage hypotheses stay local and pass through the existing bounded repair pipeline."""


from decompnet.app import stack_repairs


def test_unused_scalar_ignores_field_names_and_keeps_lines_literals_and_other_functions():
    source = 'int other(void) { return 7; }\nvoid f(Obj *p)\n{\n int flags; /* flags stays a comment */\n p->flags=1;\n say("flags");\n}\n'
    variants = stack_repairs.variants(source, 'f')
    repaired = next(text for family,text in variants if family == 'unused_scalar_local')
    assert 'int flags;' not in repaired and 'p->flags=1;' in repaired
    assert '/* flags stays a comment */' in repaired and 'say("flags")' in repaired
    assert repaired.count('\n') == source.count('\n')
    assert repaired.startswith('int other(void) { return 7; }\n')


def test_live_address_taken_and_sizeof_locals_are_not_removed_or_made_register():
    for expression in ['use(&x);', 'use(sizeof(x));', 'return x;']:
        variants = stack_repairs.variants('int f(void) { int x; '+expression+' }', 'f')
        assert not any(f == 'unused_scalar_local' for f,_ in variants)
        if '&x' in expression:
            assert not any(f == 'local_storage' for f,_ in variants)


def test_initialized_volatile_aggregate_and_preprocessor_storage_are_not_guessed():
    for declaration in ['volatile int x;', 'int x=side_effect();', 'Obj x;']:
        assert not stack_repairs.variants('void f(void) { '+declaration+' use(x); }', 'f')
    assert not stack_repairs.variants('void f(void) { int x;\n#if X\nuse(x);\n#endif\n}', 'f')


def test_pointer_and_scalar_parameters_support_register_without_touching_prototypes():
    src = 'int f(int n, Obj *p);\nint f(int n, Obj *p) { int i; i=n; return p->x+i; }'
    variants = stack_repairs.variants(src, 'f')
    params = [text for family,text in variants if family == 'parameter_storage']
    assert any('int f(register int n, Obj *p)' in text for text in params)
    assert any('int f(int n, register Obj *p)' in text for text in params)
    assert all(text.startswith('int f(int n, Obj *p);\n') for text in params)
    assert not any(f == 'parameter_storage' for f,_ in stack_repairs.variants('void f(int x) { use(&x); }', 'f'))


def test_unused_declaration_keeps_embedded_comments():
    src='void f(void) { int /* field note */ unused; use(); }'
    candidate=next(t for f,t in stack_repairs.variants(src,'f') if f=='unused_scalar_local')
    assert '/* field note */' in candidate and 'unused' not in candidate
