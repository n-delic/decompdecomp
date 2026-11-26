"""One header is parsed once however many functions ask about it, in bounded memory."""
from decompnet.app import prototype_alignment as alignment

HEADER = 'int f(int x);\n'
SOURCE = 'short f(int x){return x*3;}'


def counted(monkeypatch):
    calls = []
    original = alignment._prepare
    monkeypatch.setattr(alignment, '_prepare', lambda header: calls.append(header) or original(header))
    monkeypatch.setattr(alignment, '_cache', type(alignment._cache)())
    return calls


def test_equal_headers_are_parsed_once_and_a_changed_one_again(monkeypatch):
    calls = counted(monkeypatch)
    first = alignment.prepare(HEADER)
    assert alignment.prepare(''.join(list(HEADER))) is first  # by content, not by object
    for _ in range(3):
        assert alignment.variants(SOURCE, 'f', HEADER) == ['int f(int x){return x*3;}']
    assert len(calls) == 1
    assert alignment.variants(SOURCE, 'f', 'char f(int x);\n') == ['char f(int x){return x*3;}']
    assert len(calls) == 2


def test_the_cache_keeps_the_recently_used_headers_within_both_limits(monkeypatch):
    calls = counted(monkeypatch)
    monkeypatch.setattr(alignment, 'CACHE_ENTRIES', 2)
    one, two, three = ('int a(void);', 'int b(void);', 'int c(void);')
    for header in (one, two, one, three):
        alignment.prepare(header)
    assert len(alignment._cache) == 2 and len(calls) == 3
    alignment.prepare(one)
    assert len(calls) == 3
    alignment.prepare(two)
    assert len(calls) == 4
    monkeypatch.setattr(alignment, 'CACHE_CHARACTERS', len(one) + 1)
    alignment.prepare(three)
    assert sum(len(value[0]) for value in alignment._cache.values()) <= len(one) + 1
    huge = 'int d(void);' * 10
    assert alignment.prepare(huge)[1]  # too big to keep, still answered
    assert len(alignment._cache) == 1
