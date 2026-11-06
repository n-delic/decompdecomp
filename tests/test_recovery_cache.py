"""The engine caches each function's classes/structs extraction keyed by a content token, so a layout change
(which drops the memoized result in _xrefs) only re-extracts the functions whose bytes actually changed.
"""
from decompnet import classes, structs
from decompnet.app.engine import RomNetwork


def _engine():
    eng = RomNetwork.__new__(RomNetwork)
    eng._scan_cache = {}
    eng._facts_cache = {}
    return eng


def test_scans_reuses_unchanged_functions_and_reextracts_changed(monkeypatch):
    eng = _engine()
    seen = []
    monkeypatch.setattr(classes, "scan", lambda code, relocs, be: seen.append(code) or ("scan", code))
    corpus0 = [(1, b"aa", [], b"tok1"), (2, b"bb", [], b"tok2")]
    out0 = eng._scans(99, corpus0, False)
    assert out0 == {1: ("scan", b"aa"), 2: ("scan", b"bb")} and len(seen) == 2
    # fn 2's bytes change (new token), fn 1 unchanged; only fn 2 is re-scanned
    corpus1 = [(1, b"aa", [], b"tok1"), (2, b"BB", [], b"tok2x")]
    out1 = eng._scans(99, corpus1, False)
    assert out1[1] is out0[1]                      # same cached scan object for the unchanged function
    assert out1[2] == ("scan", b"BB") and len(seen) == 3


def test_scans_prunes_functions_that_vanished(monkeypatch):
    eng = _engine()
    monkeypatch.setattr(classes, "scan", lambda code, relocs, be: ("scan", code))
    eng._scans(99, [(1, b"aa", [], b"t1"), (2, b"bb", [], b"t2")], False)
    eng._scans(99, [(1, b"aa", [], b"t1")], False)  # fn 2 gone
    assert set(eng._scan_cache[99]) == {1}


def test_facts_reextracts_only_changed_and_merges_cache(monkeypatch):
    eng = _engine()
    calls = []

    def fake_extract(funcs, read_facts, datum, *, only=None):
        calls.append(set(only) if only is not None else None)
        return {v: ("facts", v) for v, _, _ in funcs if only is None or v in only}

    monkeypatch.setattr(structs, "extract", fake_extract)
    corpus0 = [(1, b"aa", [], b"t1"), (2, b"bb", [], b"t2")]
    out0 = eng._facts(99, corpus0, read_facts=None, datum=None)
    assert out0 == {1: ("facts", 1), 2: ("facts", 2)} and calls[0] == {1, 2}
    corpus1 = [(1, b"aa", [], b"t1"), (2, b"BB", [], b"t2x")]  # fn 2 changed
    out1 = eng._facts(99, corpus1, read_facts=None, datum=None)
    assert calls[1] == {2}                        # only the changed function re-extracted
    assert out1[1] is out0[1]                      # unchanged function's facts reused from cache
