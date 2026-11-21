"""The brief's "already tried at this best" table and the compile result's flag: helpers on near-matches handed
over the same dead ends hundreds of times because each saw only the last few notes."""
from tests.test_claims import _game


def test_brief_table_dedupes_by_diff_and_compiles_flag_repeats(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    a, _ = net.local_agent('a')
    b, _ = net.local_agent('b')
    get = lambda: net._function('f', 'game')
    net._record(get(), a, 12, 'int f(){return 1;}', 1, mismatch_rows=[('r', 'x', 'y')])
    net._record(get(), b, 12, 'int f(){return 2;}', 1, mismatch_rows=[('r', 'x', 'y')])
    net._record(get(), b, 12, 'int f(){return 3;}', 1, mismatch_rows=[('r', 'x', 'z')])
    table = '\n'.join(net.tried_at_best(get()))
    assert 'Already tried at this best (score 12): 3 compiles, 2 distinct diffs' in table
    assert table.count('\n| ') == 4  # header, rule and two diffs
    # the compile just recorded reproduces a known diff: flagged, not refused
    fid = get()['id']
    note = net.same_score_and_seen_note(fid, b.id, 12, 'int f(){return 3;}', project_id=get()['project_id'])
    assert '' == note or 'Already judged' not in note  # its diff (z) was new
    net._record(get(), a, 12, 'int f(){return 4;}', 1, mismatch_rows=[('r', 'x', 'y')])
    note = net.same_score_and_seen_note(fid, a.id, 12, 'int f(){return 4;}', project_id=get()['project_id'])
    assert 'Already judged at this best: this same diff was reached 2 time(s) by 2 agent(s)' in note
    net._record(get(), a, 12, 'int f(){return 1;}', 1, mismatch_rows=[('r', 'x', 'q')])
    note = net.same_score_and_seen_note(fid, a.id, 12, 'int f(){return 1;}', project_id=get()['project_id'])
    assert 'this exact source' in note


def test_no_table_without_a_positive_best(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    assert net.tried_at_best(net._function('f', 'game')) == []
