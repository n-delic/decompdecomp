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
