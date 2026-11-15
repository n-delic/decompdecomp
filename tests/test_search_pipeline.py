"""Streaming work and advisory caches must preserve fresh winners and bounded cancellation."""
import sys
import threading
from dataclasses import replace

import pytest

from decompnet import compile_sessions, scoring
from decompnet.app import deep_permuter, permute, trial_bookkeeping
from decompnet.elfobj import FunctionObj, Reloc
from tests.test_deep_permuter import Log
from tests.test_permute import AGENT, FakeNet


def test_comparison_reuse_is_exact_bounded_and_independent(monkeypatch):
    target = FunctionObj('f', 'mips', True, bytes.fromhex('03e0000800000000'))
    candidate = replace(target, code=bytes.fromhex('03e0000824020001'))
    calls = []
    original = scoring._diff
    def diff(*args):
        calls.append(1);return original(*args)
    monkeypatch.setattr(scoring, '_diff', diff)
    with compile_sessions.session() as session:
        result = scoring.diff(target, candidate)
        assert result.score > 0
        result.rows.clear()
        assert scoring.diff(target, candidate).rows and len(calls) == 1
        scoring.diff(target, replace(candidate, relocs=[Reloc(0, 4, 'other', 0)]))
        scoring.diff(replace(target, code=target.code+b'\0'*4), candidate)
        assert len(calls) == 3
        # Exact matches always compare again, including repeated same-output sources.
        scoring.diff(target, target);scoring.diff(target, target)
        assert len(calls) == 5
        for n in range(200):
            scoring.diff(target, replace(candidate, name=str(n)))
        assert len(session.comparisons) <= 128
        assert session.comparison_bytes <= 8*1024*1024
    scoring.diff(target, candidate)
    assert len(calls) == 206


def generator(tmp_path, body):
    script = tmp_path/'generator.py'
    script.write_text('import sys,json,time\nfor line in sys.stdin:\n job=json.loads(line)\n'+body)
    return permute.Generator([sys.executable, '-u', str(script)])


def test_generation_yields_before_done_and_kills_obsolete_parent(tmp_path):
    gen = generator(tmp_path, ''' for n in range(8):
  print(json.dumps({'variant':'int f(){return '+str(n)+';}','lineage':['rewrite']}),flush=True)
 time.sleep(60)
''')
    stream = gen.chunks({'source':'int f(){return 0;}', 'function':'f'}, lambda: None)
    try:
        chunk = next(stream)
        assert len(chunk) == 8 and chunk[0].lineage == ['rewrite']
        assert gen.proc.poll() is None
        assert gen._lines.maxsize == 32
    finally:
        stream.close()
        gen.close()
    assert gen.proc.poll() is not None


def test_stream_reuses_generator_only_after_consuming_done(tmp_path):
    gen = generator(tmp_path, " print(json.dumps({'variant':'int f(){return 1;}'}),flush=True)\n print(json.dumps({'done':True}),flush=True)\n")
    try:
        job = {'source':'int f(){return 0;}', 'function':'f'}
        assert list(gen.chunks(job, lambda: None)) == [['int f(){return 1;}']]
        assert not gen._closed.is_set()
        assert list(gen.chunks(job, lambda: None)) == [['int f(){return 1;}']]
    finally:gen.close()


def test_waiting_generation_checks_cancellation(tmp_path):
    gen = generator(tmp_path, ' time.sleep(60)\n')
    calls = []
    def check():
        calls.append(1)
        if len(calls) == 2:raise permute.GenerationStopped()
    try:
        with pytest.raises(permute.GenerationStopped):list(gen.chunks({'source':'f','function':'f'}, check))
        assert gen.proc.poll() is not None
    finally:gen.close()


def test_generator_failure_is_visible_for_simple_rewrite_fallback(tmp_path):
    gen = generator(tmp_path, " print(json.dumps({'error':'bad source'}),flush=True)\n print(json.dumps({'done':True}),flush=True)\n")
    try:
        assert list(gen.chunks({'source':'f','function':'f'}, lambda: None)) == []
        assert gen.failed
    finally:gen.close()


def test_batched_lookup_avoids_per_candidate_reads_and_honors_pending_feedback(monkeypatch):
    net = FakeNet();fn = net.function();proj = net.project_row('game')
    search = deep_permuter.Search(net, 1, 100, Log(), fn)
    sources = ['int f(){return '+str(n)+';}' for n in range(8)]
    queries = []
    original_all, original_one = net.store.all, net.store.one
    def all_(sql, *args):
        if 'FROM reuse_trials' in sql:queries.append(sql)
        return original_all(sql, *args)
    def one(sql, *args):
        if 'FROM reuse_trials' in sql:queries.append(sql)
        return original_one(sql, *args)
    monkeypatch.setattr(net.store, 'all', all_);monkeypatch.setattr(net.store, 'one', one)
    monkeypatch.setattr(permute.Permuter, '_score', lambda *args: 40)
    search._prime_trials(AGENT, proj, fn, sources, None)
    assert len(queries) == 1 and ' IN (' in queries[0]
    with trial_bookkeeping.scope():
        for source in sources:assert search._score_current(AGENT, proj, fn, source, 40, None) == 40
        # Disable the earlier source cache: pending DB evidence must override primed absence.
        search._recent_candidates.clear()
        assert search._score_current(AGENT, proj, fn, sources[0], 40, None) == 40
        assert search._trial_reused
    assert len(queries) == 1
    # A changed header generates a new signature and falls back to a fresh lookup.
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,unit,header) VALUES(1,2,'','changed')")
    net._context = lambda *args: {'header':'changed', 'version':2}
    search._score_current(AGENT, proj, fn, sources[0], 40, None)
    assert len(queries) == 2


def test_stream_primes_each_chunk_and_closes_on_discard():
    events = []
    class Search(permute.Permuter):
        def _prime_trials(self, agent, proj, fn, sources, fork):events.append(list(sources))
    search = Search(FakeNet())
    def chunks():
        try:
            yield ['first']
            yield ['obsolete']
        finally:events.append('closed')
    stream = search._stream_variants(chunks(), AGENT, {}, {}, None)
    assert next(stream) == 'first'
    stream.close()
    assert events == [['first'], 'closed']


def test_comparison_session_is_not_shared_across_threads():
    from contextvars import copy_context
    with compile_sessions.session():
        context = copy_context();seen = []
        thread = threading.Thread(target=lambda: context.run(lambda: seen.append(compile_sessions.current())))
        thread.start();thread.join(2)
        assert seen == [None]


@pytest.mark.parametrize('mode,key', [('none','driver_native'), ('wsl','driver_wsl'), ('linux-vm','driver_linux-vm')])
def test_driver_timings_identify_execution_environment(tmp_path, monkeypatch, mode, key):
    import subprocess

    from decompnet import compile_timing, compiler
    cfg = compiler.CompilerConfig(['synthetic-cc'], sandbox=mode)
    seen = []
    def run(*args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args[0], 0, 'ok', '')
    monkeypatch.setattr(compiler, '_exec_driver', run)
    with compile_sessions.session() as session:
        assert compiler._exec(cfg, tmp_path, ['synthetic-cc'], 1).stdout == 'ok'
        assert session.metrics[key] >= 0
    assert len(seen) == 1 and key in compile_timing.snapshot()
