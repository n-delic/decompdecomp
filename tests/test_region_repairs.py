import json

from decompnet.app import region_repairs as regions


def rows(changed):
    return [('r' if i in changed else ' ', f'op {i}', f'other {i}') for i in range(48)]


def test_target_regions_recognize_a_local_gain_despite_a_larger_global_regression():
    before = regions.snapshot(rows({0, 1, 2}))
    after = regions.snapshot(rows(set(range(20, 32))))
    assert regions.gain(before, after) == 3
    assert regions.gain(json.loads(json.dumps(before)), after) == 3


def test_inserted_instructions_do_not_shift_target_region_identity():
    baseline = rows({17, 18})
    candidate = rows({17})
    candidate.insert(1, ('>', '', 'extra op'))
    assert regions.gain(regions.snapshot(baseline), regions.snapshot(candidate)) == 1
    candidate[5] = ('r', 'different target', 'other')
    assert regions.gain(regions.snapshot(baseline), regions.snapshot(candidate)) == 0
    assert regions.snapshot([]) is None


def test_preserving_correct_regions_is_advice_not_a_match_verdict():
    before = regions.snapshot(rows({1, 2}))
    assert regions.damage(before, regions.snapshot(rows({1}))) == 0
    assert regions.damage(before, regions.snapshot(rows({17, 18}))) == 2
    assert regions.damage(None, before) == 0
