"""The Deep repair lane offers the library's bounded local reuse."""
from decompnet.app import draft_repairs


def test_lane_offers_reuse_for_a_dead_return_local():
    source = '''void f(u8 *p) {
    s32 result;
    {
        s32 first = *(u16 *)(p + 2);
        s32 id = 4;
        s32 one = 1;
        sink(first, one);
        result = call(id, first);
    }
    sink(result, 0);
}
'''
    labels = {}
    proposals = draft_repairs.variants(source, 'f', 'mips', labels=labels)
    assert any(labels.get(candidate) == 'dead_local_stage'
               and 'result = *(u16 *)(p + 2);' in candidate
               and 'result = call(id, result);' in candidate for candidate in proposals)
