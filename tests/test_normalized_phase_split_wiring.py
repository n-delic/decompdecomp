"""normalized_phase_split reaches the drafts, the Deep Permuter's local lane and the agents' family list."""

from decompnet.app import draft_repairs

# The nested-walk shape of a hand-edited best: a declarator list and an object macro reading a local.
SOURCE = """#define F(p, t, o) (*(t *)((s8 *)(p) + (o)))
#define NF ((found ^ 1) & 1)
extern s32 probe(void *, void *);
void *walk(void *arg) {
    s32 found;
    void *cur, *child, *grand;
    found = 0;
    cur = F(arg, void *, 0x20);
    F(cur, s32, 4) = 1;
    if (probe(cur, arg)) found = 1;
    child = F(cur, void *, 0x58);
    while ((child != NULL) & NF) {
        grand = F(child, void *, 8);
        F(grand, s32, 4) = 2;
        if (probe(grand, arg)) found = 1;
        child = F(child, void *, 0);
    }
    cur = F(arg, void *, 0x24);
    F(cur, s32, 8) = 3;
    cur = F(arg, void *, 0x28);
    F(cur, s32, 8) = 4;
    F(cur, s32, 4) = 5;
    return cur;
}
"""


def test_the_drafts_propose_the_normalized_phase_split_and_agents_know_it():
    labels = {}
    draft_repairs.variants(SOURCE, 'walk', 'mips', labels=labels, limit=None)
    assert {'normalized_phase_split', 'normalized_phase_chain'} & set(labels.values())
    assert {'normalized_phase_split', 'normalized_phase_chain'} <= set(draft_repairs.AGENT_FAMILIES)


def test_the_deep_permuters_local_lane_runs_it_with_the_header():
    from decompnet.app import library_registry
    local = library_registry.local_calls(library_registry.draft_context(SOURCE, 'f', 'typedef int s32;', 'mips', '', ()))
    call = next(call for call in local if call[1] == ('normalized_phase_split',))
    assert call[2]['header'] == 'typedef int s32;'
