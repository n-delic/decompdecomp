import pytest

from decompnet.search_notes import useful


def test_legacy_bookkeeping_and_repeated_summaries_do_not_hide_useful_notes():
    rows = [
        {'agent': 'W3.permuter-750', 'text': 'Deep permutation turn ended; improvements shared.'},
        {'agent': 'W2.permuter-750', 'text': 'Deep permutation turn ended; improvements shared.'},
        {'agent': 'W3.permuter-750', 'text': 'Deep permuter: score 40 → 20'},
        {'agent': 'W3.permuter-750', 'text': 'Deep permuter: score 50 → 40'},
        {'agent': 'W2.permuter-750', 'text': 'Deep permuter: score 80 → 50'},
        {'agent': 'you', 'text': 'Keep the signed comparison.'},
    ]
    assert useful(rows) == [rows[2], rows[4], rows[5]]

def test_boilerplate_is_skipped_on_release_and_direct_note(tmp_path):
    from .test_claims import _game

    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('worker')
    permutation = 'Deep permutation turn ended; improvements shared.'
    limit = "User's max tries reached (5/5); best attempt retained."
    assert net.claim_row(agent, 'game', 'f')
    net.add_note(agent, 'game', permutation, 'f')
    net.add_note(agent, 'game', limit, 'f')
    useful_summary = 'Deep permuter: score 40 → 20 after reversing the branch.'
    net.add_note(agent, 'game', useful_summary, 'f')
    net.release(agent, 'f', permutation, 'game')
    assert not net.store.one('SELECT 1 FROM claims')
    assert [row['text'] for row in net.store.all('SELECT text FROM notes')] == [useful_summary]

@pytest.mark.parametrize('by_name', [True, False])
def test_room_sync_skips_boilerplate_but_keeps_findings(tmp_path, by_name):
    from .test_claims import _game

    net, _ = _game(tmp_path, [('f', 10, 'open')])
    with net.store.tx() as db:
        db.execute('UPDATE functions SET vram=123 WHERE name=?', ('f',))
    texts = [
        'Released because its agent stopped (decompdecomp closed); its attempts are kept.',
        'Deep permutation turn ended; improvements shared.',
        "User's max tries reached (5/5); best attempt retained.",
        'Keep the signed comparison.',
    ]
    for text in texts:
        if by_name:
            net.apply_remote_note_named('game', 'f', text, 'worker', 1)
        else:
            net.apply_remote_note('game', 1, 123, text, 'worker', 1)
    assert [row['text'] for row in net.store.all('SELECT text FROM notes')] == [texts[-1]]


def test_repeat_findings_are_saved_once_on_unmatched_functions(tmp_path):
    from .test_claims import _game

    net, _ = _game(tmp_path, [('f', 10, 'open'), ('matched', 10, 'matched')])
    first, _ = net.local_agent('first')
    second, _ = net.local_agent('second')
    finding = 'Keep the signed comparison.'
    for name in ('f', 'matched'):
        net.add_note(first, 'game', finding, name)
        net.add_note(second, 'game', finding, name)
    net.claim_row(second, 'game', 'f')
    net.release(second, 'f', finding, 'game')
    distinct = 'The final store also needs an unsigned cast.'
    net.add_note(second, 'game', distinct, 'f')
    rows = net.store.all('SELECT f.name,n.text FROM notes n JOIN functions f ON f.id=n.function_id ORDER BY n.id')
    assert [tuple(row) for row in rows] == [
        ('f', finding), ('matched', finding), ('matched', finding), ('f', distinct),
    ]
