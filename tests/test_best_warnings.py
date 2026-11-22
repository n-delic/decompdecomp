"""Warning signs in a saved best: advisory lines in an unmatched function's brief, never a refusal (best_warnings)."""
import struct

from decompnet import scoring
from decompnet.app.best_warnings import brief_lines, warnings
from decompnet.elfobj import FunctionObj

TWICE = """typedef struct { unsigned int w0, w1; } Gfx;
extern Gfx *cursor;
void f(int a) {
    Gfx *c = cursor;
    c[4].w0 = a;
    c[4].w0 = a + 1;
    c[5].w1 = 0;
}
"""
UNINITIALISED = """int f(int a) {
    int x;
    int y;
    y = 3;
    return a + x + y;
}
"""
VOLATILE_LOCAL = """extern void * volatile cursorp;
int f(int a) {
    volatile int t;
    void * volatile p;
    volatile unsigned int *reg;
    t = a;
    return t;
}
"""


def test_each_warning_sign_is_found():
    assert warnings(TWICE) == ["`c[4].w0` is stored twice in a row (the first value is never read)"]
    assert warnings(UNINITIALISED) == ["local `x` is read before anything sets it"]
    assert warnings(VOLATILE_LOCAL) == ["local `t` is declared volatile", "local `p` is declared volatile"]


def test_hardware_and_volatile_stores_are_never_flagged():
    source = """extern volatile unsigned int D_FIFO;
void f(int a) {
    *(unsigned int *)0xA4600010 = 3;
    *(unsigned int *)0xA4600010 = 3;
    *(volatile unsigned int *)(cursor) = a;
    *(volatile unsigned int *)(cursor) = a;
    D_FIFO = a;
    D_FIFO = 0;
    a = a + 1;
    a = a * 2;
}
"""
    assert warnings(source) == []


def fn(source, status="open", score=1825):
    return {"status": status, "best_score": score, "best_source": source}


def test_the_brief_warns_only_while_the_function_is_unmatched():
    assert "stored twice" in "\n".join(brief_lines(fn(TWICE)))
    assert brief_lines(fn(TWICE, status="matched", score=0)) == []
    assert brief_lines(fn(TWICE, score=0)) == []
    assert brief_lines(fn("void f(void) {}\n")) == []


def test_a_matching_source_with_these_patterns_still_scores_zero():
    """Warnings never touch the judge: identical bytes are a match whatever the C looks like."""
    code = b"".join(struct.pack(">I", w) for w in (0xAC280020, 0xAC290020, 0x03E00008, 0))
    target, candidate = FunctionObj("f", "mips", True, code), FunctionObj("f", "mips", True, code)
    assert scoring.diff(target, candidate).score == 0


def _net(tmp_path):
    import json

    from decompnet.app.engine import RomNetwork
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', 'mips', str(tmp_path), json.dumps({'kind': 'rom'}))).lastrowid
        db.execute('INSERT INTO functions(project_id,name,n_insns,size) VALUES(?,?,?,?)', (pid, 'f', 50, 200))
    return net


def test_sources_with_each_pattern_are_saved_as_best_and_matched_normally(tmp_path, monkeypatch):
    """A best with warning signs is kept like any other, and a match with them is a match."""
    for k, source in enumerate((TWICE, UNINITIALISED, VOLATILE_LOCAL)):
        (tmp_path / str(k)).mkdir()
        net = _net(tmp_path / str(k))
        a, _ = net.local_agent('a')
        fn = net.claim_row(a, 'game', function='f')
        net._record(fn, a, 400, source, 1)
        assert net._function('f', 'game')['best_source'] == source
        assert net._function('f', 'game')['best_score'] == 400
        monkeypatch.setattr(net, 'on_match', lambda *args: None)
        net._record(net._function('f', 'game'), a, 0, source, 1, matched=True)
        row = net._function('f', 'game')
        assert row['status'] == 'matched' and row['matched_source'] == source and brief_lines(row) == []
