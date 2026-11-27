"""Candidates of one function compiled in one compiler run: each judged exactly as if compiled alone."""
import threading
from concurrent.futures import CancelledError

import pytest

from decompnet import batch_compile, compile_sessions, compiler, elfobj, toolchains
from decompnet.app import deep_compilation
from tests.test_rom_explorer import project  # noqa: F401

PREFIX = '''/* machine draft, tried by autopilot */
#ifndef M2C_DEFINES
#define M2C_DEFINES
typedef int M2C_UNK;
#define M2C_FIELD(expr, type_ptr, offset) (*(type_ptr)((char *)(expr) + (offset)))
#endif

extern int D_80100000;
'''


def body(n):
    return 'int func_80001000(int *p, int x) {\n    int i, t = 0;\n    for (i = 0; i < x; i++) t += p[i] * %d ^ D_80100000;\n    return t;\n}\n' % n


def test_definitions_are_found_after_declarations_and_prototypes():
    source = 'int func_80001000(int);\nextern int D;\nint func_80001000(int x) { return x; }\n'
    prefix, rest, line = batch_compile._definition(source, 'func_80001000')
    assert line == 2 and rest.startswith('int func_80001000(int x)') and prefix.count('\n') == 1
    assert batch_compile._code('/* a */\nextern int D;\n') == batch_compile._code('// b\n\nextern int D;')
    assert not batch_compile.eligible('int f(void) {\n#define X 1\nreturn X; }', 'f')
    assert not batch_compile.eligible('int f(void) { return __LINE__; }', 'f')


@pytest.fixture
def kmc(tmp_path, monkeypatch):
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs the pinned KMC GCC')
    from dataclasses import replace

    from decompnet.project import compiler_config
    cfg = compiler_config(tmp_path, {'kind': 'rom', 'platform': 'n64', 'big_endian': True,
                                     'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-G0', '-mips3', '-O2']}})
    return replace(cfg, identity=('batch-test',))


def code(result, name='func_80001000'):
    found = elfobj.extract_function(result.obj, name)
    return found.code, found.relocs


def test_batched_candidates_are_what_they_are_alone(kmc):
    sources = [PREFIX.replace('tried by autopilot', 'variant %d' % n) + body(n) for n in range(3, 8)]
    with compile_sessions.session():
        batched = batch_compile.compile_batch(kmc, 'typedef int s32;', sources, 'func_80001000')
        alone = [compiler.compile_source(kmc, 'typedef int s32;', s) for s in sources]
    assert all(r is not None and r.batched for r in batched)
    assert [code(b) for b in batched] == [code(a) for a in alone]


def test_conflicting_declarations_run_apart_and_a_broken_candidate_is_dropped(kmc):
    other = PREFIX.replace('extern int D_80100000;', 'extern short D_80100000;')
    broken = PREFIX + body(9).replace('return t;', 'return t +;')
    sources = [PREFIX + body(3), other + body(4), PREFIX + body(5), other + body(6), broken, PREFIX + body(7)]
    with compile_sessions.session():
        batched = batch_compile.compile_batch(kmc, '', sources, 'func_80001000')
        alone = [compiler.compile_source(kmc, '', s) for s in sources]
    assert batched[4] is None  # the broken one compiles alone, reporting its own error
    for got, expected in zip(batched, alone):
        if got is not None:
            assert code(got) == code(expected)
    assert sum(r is not None for r in batched) == 5


def test_a_function_using_its_files_own_data_compiles_alone(kmc):
    with_data = 'const char *func_80001000(int x) { return x ? "yes" : "no"; }\n'  # (its strings: its file's data)
    with compile_sessions.session():
        batched = batch_compile.compile_batch(kmc, '', [with_data, with_data.replace('"no"', '"maybe"')], 'func_80001000')
    assert batched == [None, None] or batched is None


class Net:
    def __init__(self, batches):
        self.batches = batches

    def _compile_batch(self, proj, header, sources, fn):
        self.batches.append(list(sources))
        return [None if 'alone' in s else compiler.CompileResult(True, s.encode(), '') for s in sources]


def test_the_pool_batches_one_functions_queued_candidates(monkeypatch):
    batches = []
    net = Net(batches)
    pool = deep_compilation.Pool(1, maximum=True, remote=False)
    gate = threading.Event()
    blocker = deep_compilation.Ticket('block', (), lambda: gate.wait(5) and compiler.CompileResult(True, b'b', ''),
                                      lambda: None)
    tickets = []
    for n, source in enumerate(['a', 'b', 'alone', 'c']):
        ticket = deep_compilation.Ticket(str(n), (), lambda s=source: compiler.CompileResult(True, b'alone:' + s.encode(), ''),
                                         lambda: None)
        ticket.batch = (('net', 1, 7, 'header'), net, {'id': 1}, 'header', {'name': 'f'})
        ticket.batch_source = source
        tickets.append(ticket)
    other = deep_compilation.Ticket('x', (), lambda: compiler.CompileResult(True, b'other', ''), lambda: None)
    other.batch = (('net', 1, 8, 'header'), net, {'id': 1}, 'header', {'name': 'g'})
    other.batch_source = 'x'
    try:
        pool.submit(blocker)
        for ticket in [*tickets, other]:
            pool.submit(ticket)
        gate.set()
        results = [t.future.result(timeout=10) for t in tickets]
        assert other.future.result(timeout=10).obj == b'other'  # another function: never in that batch
    finally:
        pool.close()
    assert batches and batches[0] == ['a', 'b', 'alone', 'c']
    assert [r.obj for r in results] == [b'a', b'b', b'alone:alone', b'c']
    assert CancelledError  # (imported for the pool's cancelled-ticket path)


def test_rust_judges_real_random_rewrite_batches_against_python(project, monkeypatch):  # noqa: F811
    import json

    from decompnet import scoring
    from decompnet.app import mcp_gateway
    from decompnet.rom import match

    if not (gateway := mcp_gateway.built()) or not scoring._native_mips_decoder_compatible():
        pytest.skip("needs the Rust gateway and matching Capstone")
    if not toolchains.installed("gcc-2.7.2-kmc"):
        pytest.skip("needs KMC GCC")

    net, pid, root = project
    monkeypatch.setenv("DECOMP_MCP_GATEWAY", str(gateway))
    proj = net.project_row("game", sync=False)
    cfg = json.loads(proj["config"])
    cfg["compiler"]["flags"] = ["-O2", "-G0"]
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), pid))
    proj = net.project_row("game", sync=False)
    fn = net._function("reader", "game")
    sources = [f"int reader(int x) {{ return x < 0 ? x - {i} : x + {i}; }}" for i in range(1, 9)]
    exact = net._compile(proj, "", sources[0], fn)
    assert exact.ok, exact.log
    target = elfobj.extract_function(exact.obj, "reader")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=?, relocs='[]', size=?, n_insns=? WHERE id=?",
                   (target.code, len(target.code), len(target.code) // 4, fn["id"]))
    fn = net._function("reader", "game")
    group = []
    for source in sources:
        ticket = deep_compilation.Ticket(source, (), lambda: pytest.fail("expected a shared compiler run"), lambda: None)
        ticket.batch = ((pid, fn["id"]), net, proj, "", fn)
        ticket.batch_source = source
        ticket.random_rewrite = True
        assert ticket.future.set_running_or_notify_cancel()
        group.append(ticket)
    pool = deep_compilation.Pool(1, maximum=True, remote=False)
    with compile_sessions.session():
        pool._run_batch(group)

    outcomes = []
    for ticket in group:
        result = ticket.future.result(timeout=10)
        assert result.batched and ticket.batch_judgment is not None
        batch = ticket.batch_judgment
        candidate, diff, clean = batch.candidate, batch.diff, batch.clean
        assert batch.compiled is result and batch.scope == net._random_batch_scope(fn, proj)
        original = match.link(result.obj, fn["name"], net._rom_target(fn), net.symbols(pid), net.reader(proj, fn))
        expected = scoring._diff(net._target_obj(fn, proj), original.candidate, details=False)
        assert candidate == original.candidate and clean == (not original.problems)
        assert (diff.score, diff.bytes_match, diff.max_score, diff.rows) == (
            0 if original.exact else max(1, expected.score), original.exact, expected.max_score, [],
        )
        outcomes.append(diff.bytes_match)
    assert outcomes == [True] + [False] * 7
    with (root / "game.rom").open("ab") as stream:
        stream.write(b"new image identity")
    assert group[0].batch_judgment.scope != net._random_batch_scope(fn, proj)
