"""Only reachable declarations from saved C can affect the alternative draft."""
from decompnet.app.draft_context import candidate_context


def test_local_prototype_replaces_shared_and_keeps_type_dependencies():
    header = 'typedef signed short Half; int sample(int x); int unrelated;'
    source = 'Half sample(int x); int f(int x){ return sample(x); }'
    out = candidate_context(header, source, {'sample', 'f'})
    assert 'typedef signed short Half;' in out
    assert 'Half sample(int x);' in out
    assert 'int sample(int x);' not in out
    assert 'return' not in out and 'unrelated' not in out


def test_bodies_macros_initializers_and_unreachable_declarations_are_excluded():
    source = '#define BAD 1\nint helper(){return 42;} int data=123; float unused(int); short wanted(int);'
    out = candidate_context('', source, {'wanted', 'helper', 'data'})
    assert out.strip() == 'short wanted(int);'
    assert not candidate_context('', 'void f(){ short local(int); }', {'local'})


def test_identical_overlay_and_partial_multi_global_conflict_are_skipped():
    assert not candidate_context('short sample(int);', 'short sample(int);', {'sample'})
    assert not candidate_context('extern int a,b;', 'extern short a;', {'a', 'b'})


def test_transitive_local_and_shared_type_dependencies():
    out = candidate_context('typedef int Number; int sample(int);',
                            'typedef Number Small; Small sample(int);', {'sample'})
    assert 'typedef int Number;' in out and 'typedef Number Small;' in out
    assert 'Small sample(int);' in out


def test_cxx_and_oversized_sources_are_not_passed_to_m2c():
    assert not candidate_context('', 'class Thing { public: int f(); };', {'Thing'})
    assert not candidate_context('', ' ' * 128001, {'f'})


def test_only_target_definition_is_reduced_to_a_prototype():
    source = 'int helper(void){return 7;} short target(int x){return helper()+x;}'
    out = candidate_context('', source, {'helper','target'}, 'target')
    assert out.strip() == 'short target(int x);'
    assert 'return' not in out and 'helper' not in out
