"""Returning branches can have independent pointer live ranges without renaming member fields."""

import pytest

from decompnet.app.branch_locals import variant
from decompnet.app.draft_repairs import variants
from decompnet.app.permute import Permuter, State
from tests.test_permute import AGENT, FakeNet, ScriptedPermuter

SOURCE = '''void f(int n) {
void *p;
void *manager;
if (n == 1) { manager = shared; p = other; send(manager, p); return; }
if (n == 2) { manager = shared; p = other; send(manager, p); return; }
p = third; send(p);
}'''


def test_localizes_only_returning_branches_and_preserves_later_pointer():
    out = variant(SOURCE, 'f')
    assert 'void *__branch_p;' in out and 'void *__branch_p_;' in out
    assert 'send(__branch_manager, __branch_p);' in out
    assert 'p = third; send(p);' in out
    assert variants(SOURCE, 'f', 'mips')[0] == out
    assert variants(SOURCE, 'f', 'x86_64')[0] == out


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


def test_large_deterministic_candidate_runs_without_lifting_random_cutoff():
    net = FakeNet()
    with net.store.tx() as db:
        db.execute('UPDATE functions SET n_insns=779,best_score=175,best_source=?', (SOURCE,))
    perm = ScriptedPermuter(net, {variant(SOURCE, 'f'): 0})
    project = net.project_row('game')
    assert Permuter(net).candidates(project) == []
    state = State(); perm._repair_round(AGENT, project, state)
    assert state.matched == 1 and len(perm.judged) == 1
    assert net.function()['permuted'] == 0
    with net.store.tx() as db:
        db.execute('UPDATE functions SET n_insns=1025')
    perm._repair_round(AGENT, project, State())
    assert len(perm.judged) == 1
