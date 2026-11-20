"""Repeat compiles. In one project 3,639 of 9,611 compiles were byte-identical to an earlier one of the same function,
and in two long sessions 21 of 56 compiles only reproduced an earlier score (1001 seven times on one function).
A source the same as one this function already compiled, in the same state, gets that compile's result back, marked
as a repeat, without a build and without counting toward the move-on number; a new variant that only reproduces an
earlier score of the claim says which compile it equals."""

import time

from decompnet.network import normalized_source

from .test_claims import _game


def _record(net, agent, name, score, source):
    fn = net.store.one("SELECT * FROM functions WHERE name=?", name)
    net._record(fn, agent, score, source, 1)
    return fn


def test_sources_compare_without_indentation_but_keep_their_lines():
    a = "int f(void) {\n    return 1;   \n}\n\n"
    b = "int f(void) {\n\treturn 1;\n}"
    assert normalized_source(a) == normalized_source(b)
    assert normalized_source("int f(void) {\n\n return 1;\n}") != normalized_source(b)  # a line more: __LINE__
    assert normalized_source('char *s = "a\\\n   b";') != normalized_source('char *s = "a\\\nb";')  # inside a string


def test_a_repeat_returns_the_earlier_result_and_doesnt_count(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    fn = _record(net, a, "f", 40, "int f(){return 1;}")
    key = net.compile_key(fn, "header v1", "int f(){return 1;}")
    assert net.repeat_of(key, a, fn) is None
    net.remember_compile(key, a, fn, "score 40 ... the diff", 40)
    again = net.compile_key(fn, "header v1", "    int f(){return 1;}   \n")
    res = net.repeat_of(again, a, fn)
    assert res["repeat"] and res["score"] == 40 and "REPEAT" in res["text"] and "your compile #1 of f" in res["text"]
    assert "score 40 ... the diff" in res["text"] and "doesn't count toward the move-on number" in res["text"]
    assert net.store.one("SELECT COUNT(*) AS n FROM attempts")["n"] == 1  # nothing recorded
    assert net.compile_key(fn, "header v2", "int f(){return 1;}") != key  # another header: built again
    net.bump_generation(fn["project_id"])  # an edit_file, a rename, a new compiler...
    assert net.compile_key(fn, "header v1", "int f(){return 1;}") != key


def test_events_that_change_what_compiles_depend_on_start_a_new_generation(tmp_path):
    net, pid = _game(tmp_path, [("f", 10, "open")])
    fn = net.store.one("SELECT * FROM functions WHERE name='f'")
    key = net.compile_key(fn, "src")
    with net.store.tx() as db:
        net._event(db, pid, None, "claim", "f")
    assert net.compile_key(fn, "src") == key
    with net.store.tx() as db:
        net._event(db, pid, None, "rename", "f")
    assert net.compile_key(fn, "src") != key


def test_a_variant_that_reproduces_an_earlier_score_says_which_compile_it_equals(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    time.sleep(0.01)
    _record(net, a, "f", 1001, "int f(){return 1;}")
    _record(net, a, "f", 900, "int f(){return 2;}")
    fn = _record(net, a, "f", 1001, "int f(){return 3;}")
    note = net.same_score_note(fn["id"], a.id, 1001, "int f(){return 3;}")
    assert "Same score as your compile #1 of this claim (1001)" in note
    assert net.same_score_note(fn["id"], a.id, 900, "int f(){return 2;}") == ""  # its own row only


def test_repeat_judges_actual_object_across_identical_context_versions(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet import scoring

    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('helper')
    fn = net._function('f', 'game')
    version = 1
    header = 'typedef int word;'
    obj = b'first object'
    judged = []
    monkeypatch.setattr(net, '_context', lambda *args: {'version': version, 'header': header})
    monkeypatch.setattr(net, '_attempt_compilation', lambda *args: SimpleNamespace(ok=True, obj=obj, log=''))

    def evaluate(fn, proj, candidate):
        judged.append(candidate)
        return SimpleNamespace(fingerprint=lambda: 'candidate'), False, []

    monkeypatch.setattr(net, '_evaluate', evaluate)
    monkeypatch.setattr(net, '_score_candidate', lambda *args: scoring.DiffResult(5, 1000, [], False))
    monkeypatch.setattr(net, '_search_rows', lambda *args: [])
    import decompnet.source_lines
    monkeypatch.setattr(decompnet.source_lines, 'mismatches', lambda *args, **kwargs: {'available': False})
    source = 'int f(void){return 1;}'
    assert net._attempt_unlimited(agent, 'f', source, 'game')['score'] == 5
    version = 2
    assert net._attempt_unlimited(agent, 'f', source, 'game')['repeat']
    assert judged == [obj]
    assert net.project_store(fn['project_id']).one('SELECT COUNT(*) n FROM attempts')['n'] == 1

    obj = b'changed object'
    assert not net._attempt_unlimited(agent, 'f', source, 'game').get('repeat')
    header = 'typedef long word;'
    assert not net._attempt_unlimited(agent, 'f', source, 'game').get('repeat')
    assert len(judged) == 3
