"""Measure queued synthetic deep-search candidates through the real compiler, ROM linker and history.

Run with PYTHONPATH=src and TMPDIR on a scratch volume. No user project is opened and
no tools are downloaded. GUI serialization is excluded. Full search uses the real
generator; controlled replay uses a fixed proposal pool.
"""
import argparse
import hashlib
import importlib
import json
import os
import random
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from decompnet import compile_sessions, compile_timing, compiler, elfobj, toolchains
from decompnet.app import deep_compilation, deep_permuter, deep_throughput, search_history, trial_bookkeeping
from decompnet.app.engine import RomNetwork
from decompnet.project import compiler_config
from decompnet.repair_feedback import trial


class Log:
    def __call__(self, line):pass
    def progress(self, value):pass
    def check_cancel(self):pass


def ranked_replay(rows, variants, backend, helper):
    """Freeze one proposal pool per function and rank it before any trial compiles."""
    randomizer = random.Random(51)
    proposals = {}
    features, targets = [], []
    for fn in rows:
        upper, lower = (int.from_bytes(fn['target'][i:i+4], 'big') for i in (0, 4))
        if upper >> 16 != 0x3c02 or lower >> 16 != 0x3442:
            raise RuntimeError('Expected the synthetic KMC lui/ori pair.')
        constant = ((upper & 0xffff) << 16) | (lower & 0xffff)
        alternatives = randomizer.sample(range(65536, 2**31), variants)
        alternatives.remove(constant if constant in alternatives else alternatives[-1])
        alternatives.insert(randomizer.randrange(variants), constant)
        proposals[fn['id']] = alternatives
        features.extend(alternatives)
        targets.extend([constant] * variants)
    if backend == 'current':
        return proposals, {}
    started = time.monotonic()
    response = subprocess.run(
        [str(helper), backend], input=json.dumps(dict(candidates=features, targets=targets)),
        text=True, capture_output=True, check=True, timeout=60,
    )
    metrics = json.loads(response.stdout)
    scores = metrics.pop('scores')
    if scores != [(candidate ^ target).bit_count() for candidate, target in zip(features, targets)]:
        raise RuntimeError('Ranker disagrees with the reference CPU scores.')
    for index, alternatives in enumerate(proposals.values()):
        start = index * variants
        alternatives[:] = [value for _, value in sorted(
            zip(scores[start:start+variants], alternatives), key=lambda item: item[0]
        )]
    return proposals, {**metrics, 'rank_seconds_with_verification': time.monotonic()-started}


def benchmark(root, functions, variants, workers, slots, declarations, full_search=False, balanced=False, profile=False, search_seconds=60, replay_order=None, ranker=None, source_statements=0, local_batches=False):
    if full_search:
        # Load the legacy module alias before worker threads can import it concurrently.
        importlib.import_module('decompnet.arch.mips_runtime')
    net = RomNetwork(root/'benchmark.db', root)
    cfg = dict(kind='rom', platform='n64', big_endian=True,
               compiler=dict(toolchain='gcc-2.7.2-kmc', flags=['-O2','-G0','-mips3','-mgp32','-mfp32']))
    if local_batches:
        image = bytes(16)
        (root / 'game.rom').write_bytes(image)
        cfg['rom'] = {'file': 'game.rom'}
        cfg['segments'] = [{'name': 'main', 'rom_start': 0, 'rom_end': len(image), 'vram': 0x80001000}]
    header = '\n'.join(f'extern int helper_{i}(int);' for i in range(declarations))
    result = compiler.compile_source(compiler_config(root, cfg), '', 'int f(int x){return x+999999;}')
    if not result.ok:raise RuntimeError(result.log)
    target = elfobj.extract_function(result.obj, 'f')
    # Volatile-free but nontrivial C gives the source scanners a representative body.
    statements = ''.join(f'x ^= {i}; x ^= {i}; /* neutral edit */\n'
                         for i in range(1, source_statements+1))

    def source(name, constant):
        return f'int {name}(int x){{{statements}return x+{constant};}}'

    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(1,'bench','Synthetic','mips',1,?,?)",
                   (str(root),json.dumps(cfg)))
        db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(1,'',1,?)", (header,))
        # Start with a conservative score, never a fabricated near-match that could
        # overwrite the freshly measured baseline when a search reloads its row.
        for i in range(functions):
            name = f'f_{i}'
            db.execute("INSERT INTO functions(project_id,name,status,n_insns,size,target,relocs,asm,best_source,best_score,vram,rom_offset) "
                       "VALUES(1,?,'open',4,?,?,'[]','',?,400,?,0)",
                       (name,len(target.code),target.code,source(name, 1),0x80001000+i*256))
    # These functions contain no data references: the real linker needs no backing image reads.
    net._roms[1] = (b'', [])
    agent, _ = net.local_agent('permuter-benchmark')
    proj = net.store.one('SELECT * FROM projects WHERE id=1')
    rows = net.store.all('SELECT * FROM functions ORDER BY id')
    pool = deep_compilation.Pool(slots, maximum=not balanced, remote=not local_batches)
    syncs = {'forced':0,'ordinary':0};lock = threading.Lock();sync = net.progress.sync
    def observed(slug, **kwargs):
        with lock:syncs['forced' if kwargs.get('force') else 'ordinary'] += 1
        return sync(slug, **kwargs)
    net.progress.sync = observed
    def work(fn):
        if full_search:
            import cProfile
            profiler = cProfile.Profile() if profile and fn["id"] == 1 else None
            if profiler:profiler.enable()
            output = Log();output.compile_pool = pool;output.maximum_seconds = search_seconds
            try:
                result = deep_permuter.run(net, "bench", fn["name"], (search_seconds+59)//60, variants, output)
                return {k:result.get(k) for k in ("function","reason","tested","skipped","local_search","starting_score","best_score")}
            finally:
                if profiler:
                    profiler.disable();profiler.dump_stats(str(root.parent/("deep-profile-"+str(fn["id"])+".prof")))
        log = Log();log.compile_pool = pool
        name = fn['name']
        constants = proposals[fn['id']] if replay_order else range(2, variants+2)
        sources = [source(name, constant) for constant in constants]
        scores = []
        entered = time.monotonic()
        with (deep_throughput.scope(pool), compile_sessions.session(), trial_bookkeeping.scope(maximum=True),
              search_history.function_turn(log, net.store, fn['id'])):
            search = deep_permuter.Search(net, 10, variants*2, log, fn)
            search.compile_pool = pool
            chunks = (sources[i:i+slots*2] for i in range(0,len(sources),slots*2))
            stream = search._stream_variants(chunks, agent, proj, fn, None)
            try:
                for candidate in stream:
                    with trial({'kind':'random rewrite','family':'benchmark'}):
                        score = search._score(agent, proj, fn, candidate, 1, None)
                    if score is None or (score <= 0 and not replay_order):
                        raise RuntimeError('Synthetic candidate did not reach the ordinary judge.')
                    scores.append(score)
                    if score == 0:
                        return dict(matched=True, tested=len(scores), judge_seconds=time.monotonic()-entered,
                                    elapsed_to_match=time.monotonic()-started)
            finally:
                stream.close()
        if replay_order:
            raise RuntimeError('The exact candidate did not match through the ordinary judge.')
        return scores
    started = time.monotonic()
    try:
        proposals, rank_metrics = ranked_replay(rows, variants, replay_order, ranker) if replay_order else ({}, {})
        with compiler._SLOTS.capacity(slots), ThreadPoolExecutor(workers) as lanes:
            results = list(lanes.map(work, rows))
        seconds = time.monotonic()-started
        metrics = pool.metrics.snapshot()
        history = net.store.one('SELECT COUNT(*) AS n FROM reuse_trials')['n']
        digest_rows = [(row['matched'], row['tested']) for row in results] if replay_order else results
        judged = sum(len(scores) for scores in results) if not (full_search or replay_order) else None
        timings = compile_timing.snapshot()
        return dict(judged_candidates_per_second=judged/seconds if judged is not None else None,
                    batch_judged=timings.get('batch_judged', 0),
                    combined_elf_batches=timings.get('combined_elf_batches', 0),
                    batch_misses_used=timings.get('batch_judge_misses_used', 0),
                    batch_rechecks=timings.get('batch_rechecks', 0),
                    functions=functions, variants=variants, workers=workers, slots=slots,
                    header_bytes=len(header.encode()), source_statements=source_statements, seconds=seconds,
                    compiler_invocations=metrics['pool_compiler_invocations'],
                    compiles_per_second=metrics['pool_compiler_invocations']/seconds,
                    history_rows=history, scores_sha256=hashlib.sha256(json.dumps(digest_rows).encode()).hexdigest(),
                    synchronization_calls=syncs, throughput=metrics, search_results=results if full_search else None,
                    **(dict(replay_order=replay_order, rank_metrics=rank_metrics, replay_results=results)
                       if replay_order else {}))
    finally:
        pool.close()
        net.store.conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-search", action="store_true", help="Run real preparation, preflight and repair generation too.")
    parser.add_argument("--balanced", action="store_true")
    parser.add_argument("--search-seconds", type=int, default=60, help="Per-function full-search deadline.")
    parser.add_argument("--profile", action="store_true", help="Profile the first full-search function beside the scratch folder.")
    parser.add_argument('--functions', type=int, default=16)
    parser.add_argument('--variants', type=int, default=8)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--slots', type=int, default=4)
    parser.add_argument('--declarations', type=int, default=3000)
    parser.add_argument('--replay-order', choices=('current', 'cpu', 'metal'),
                        help='Replay a fixed synthetic proposal pool with one known exact variant per function.')
    parser.add_argument('--source-statements', type=int, default=0, help='Neutral statement pairs per function body.')
    parser.add_argument('--local-batches', action='store_true',
                        help='Compile local batches and give the native judge a scratch ROM image.')
    parser.add_argument('--ranker', type=Path, help='Compiled deep_candidate_metal helper.')
    args = parser.parse_args()
    if args.replay_order and args.full_search:
        parser.error('Choose full search or controlled replay.')
    if args.replay_order in ('cpu', 'metal') and args.ranker is None:
        parser.error('Pass the compiled --ranker for CPU or Metal replay.')
    if args.ranker:
        args.ranker = args.ranker.resolve()
    if not (1<=args.functions<=256 and 1<=args.variants<=256 and 1<=args.workers<=256
            and 1<=args.slots<=256 and 0<=args.declarations<=10000 and 0<=args.source_statements<=500 and 1<=args.search_seconds<=7200):
        parser.error('Use 1–256 functions, variants, workers and slots; 0–10000 declarations and 0–500 statement pairs.')
    if not toolchains.installed('gcc-2.7.2-kmc'):
        parser.error('Install the pinned gcc-2.7.2-kmc compiler first; this tool never downloads it.')
    # Repair-outcome analysis runs asynchronously, outside the measured search.
    os.environ['DECOMP_REPAIR_ANALYSIS'] = 'off'
    with tempfile.TemporaryDirectory(prefix='deep-pipeline-benchmark-') as scratch:
        print(json.dumps(benchmark(Path(scratch), **vars(args)), indent=2))


if __name__ == '__main__':main()
