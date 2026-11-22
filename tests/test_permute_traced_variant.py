"""A generated variant keeps its rewrite history when copied or pickled (it crosses worker boundaries)."""
import copy
import pickle
import random

from decompnet.app.permute import RewriteRandom, TracedVariant
from decompnet.permuter import mutate


def test_traced_variant_survives_copy_and_pickle():
    variant = TracedVariant("int f(void) { return 1; }", ["perm_commutative"])
    for clone in (copy.copy(variant), copy.deepcopy(variant), pickle.loads(pickle.dumps(variant))):
        assert clone == variant
        assert clone.lineage == ["perm_commutative"]


def test_traced_variant_lineage_is_optional():
    assert TracedVariant("x").lineage == ()

def test_simple_rewrite_reports_the_selected_family_without_changing_output():
    source = """int f(int n) {
    int a = n;
    a += 1;
    return a;
}"""
    traced = RewriteRandom(42).variant(source, '', '')
    assert traced == mutate(source, random.Random(42), header='')
    assert len(traced.lineage) == 1 and traced.lineage[0].startswith('simple_')
