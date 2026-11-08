"""Returning branches can have independent pointer live ranges without renaming member fields."""


import pytest

from decompnet.app.branch_locals import variant

SOURCE = '''void f(int n) {
void *p;
void *manager;
if (n == 1) { manager = shared; p = other; send(manager, p); return; }
if (n == 2) { manager = shared; p = other; send(manager, p); return; }
p = third; send(p);
}'''


def test_does_not_rename_member_fields_or_comments():
    src = 'void f(int x) { void *p; if(x) { p = global; obj->p = p; /* p */ return; } }'
    out = variant(src, 'f')
    assert 'obj->p = __branch_p' in out and '/* p */' in out


@pytest.mark.parametrize('body', ['p = global; send(p);', 'p = global; save(&p); return;',
                                  'p = global; goto end; return;'])
def test_rejects_fallthrough_address_escape_and_gotos(body):
    assert variant(f'void f(int x) {{ void *p; if(x) {{ {body} }} }}', 'f') is None


def test_qualified_or_initialized_locals_are_not_retyped():
    for declaration in ['volatile void *p;', 'void * volatile p;', 'const void *p;', 'void *p = global;']:
        assert variant(f'void f(int x) {{ {declaration} if(x) {{ p = other; send(p); return; }} }}', 'f') is None
