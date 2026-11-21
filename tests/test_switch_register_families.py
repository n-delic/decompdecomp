"""The two register-near-miss families for a large switch (decomprepair 0.5.146) reach the app's proposals and ranking.
Homemade example; no game source or expected game bytes."""
from decompnet.app import (  # noqa: F401 - imported to catch a bad wiring
    allocation_evidence,
    draft_repairs,
    repair_ranking,
)
from decomprepair import case_temporary_repairs, phantom_local_repairs

SOURCE = '''void f(s32 arg0, s32 *out) {
    u32 a;
    u32 b;

    switch (arg0 & 3) {
    case 0:
        a = arg0 + 1;
        out[0] = a;
        break;
    case 1:
        a = arg0 + 2;
        out[1] = a;
        break;
    case 2:
        b = arg0 + 3;
        out[2] = b;
        break;
    default:
        break;
    }
}
'''


def test_the_proposals_are_generated_and_labelled_by_family():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    found = set(labels.values())
    assert {'case_temporary_split', 'case_temporary_merge', 'phantom_wide_local'} <= found
    assert any('phantom_wide' in text for text, family in labels.items() if family == 'phantom_wide_local')


def test_the_phantom_local_is_only_proposed_for_mips():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'ppc', labels=labels, limit=None)
    assert 'phantom_wide_local' not in labels.values()
    assert 'case_temporary_split' in labels.values()


def test_the_families_are_registered_for_agents_and_ranked_for_register_near_misses():
    families = set(case_temporary_repairs.FAMILIES) | set(phantom_local_repairs.FAMILIES)
    assert families <= set(draft_repairs.AGENT_FAMILIES)
    import inspect
    assert all(f"'{family}'" in inspect.getsource(repair_ranking) for family in families)
