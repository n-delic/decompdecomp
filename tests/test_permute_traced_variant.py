"""A generated variant keeps its rewrite history when copied or pickled (it crosses worker boundaries)."""
import copy
import pickle

from decompnet.app.permute import TracedVariant


def test_traced_variant_survives_copy_and_pickle():
    variant = TracedVariant("int f(void) { return 1; }", ["perm_commutative"])
    for clone in (copy.copy(variant), copy.deepcopy(variant), pickle.loads(pickle.dumps(variant))):
        assert clone == variant
        assert clone.lineage == ["perm_commutative"]


def test_traced_variant_lineage_is_optional():
    assert TracedVariant("x").lineage == ()
