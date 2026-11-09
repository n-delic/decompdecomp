"""Measure queued synthetic deep-search candidates through the real compiler, ROM linker and history.

Run with PYTHONPATH=src and TMPDIR on a scratch volume. No user project is opened and
no tools are downloaded. Candidate generation and GUI serialization are excluded.
"""
import argparse
import hashlib
import json
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from decompnet import compile_sessions, compiler, elfobj, toolchains
from decompnet.app import deep_compilation, deep_permuter, deep_throughput, trial_bookkeeping
from decompnet.app.engine import RomNetwork
from decompnet.project import compiler_config
from decompnet.repair_feedback import trial


class Log:
    def __call__(self, line):pass
    def progress(self, value):pass
    def check_cancel(self):pass


def benchmark(root, functions, variants, workers, slots, declarations):
    net = RomNetwork(root/'benchmark.db', root)
    cfg = dict(kind='rom', platform='n64', big_endian=True,
               compiler=dict(toolchain='gcc-2.7.2-kmc', flags=['-O2','-G0','-mips3','-mgp32','-mfp32']))
    header = '\n'.join(f'extern int helper_{i}(int);' for i in range(declarations))
    result = compiler.compile_source(compiler_config(root, cfg), '', 'int f(int x){return x+999999;}')
    if not result.ok:raise RuntimeError(result.log)
    target = elfobj.extract_function(result.obj, 'f')
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(1,'bench','Synthetic','mips',1,?,?)",
                   (str(root),json.dumps(cfg)))
        db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(1,'',1,?)", (header,))
        for i in range(functions):
            name = f'f_{i}'
            db.execute("INSERT INTO functions(project_id,name,status,n_insns,size,target,relocs,asm,best_source,best_score,vram,rom_offset) "
                       "VALUES(1,?,'open',4,?,?,'[]','',?,1,?,0)",
                       (name,len(target.code),target.code,f'int {name}(int x){{return x+1;}}',0x80001000+i*256))
    # These functions contain no data references: the real linker needs no backing image reads.
    net._roms[1] = (b'', [])
    agent, _ = net.local_agent('permuter-benchmark')
    proj = net.store.one('SELECT * FROM projects WHERE id=1')
    rows = net.store.all('SELECT * FROM functions ORDER BY id')
    pool = deep_compilation.Pool(slots, maximum=True)
    log = Log();log.compile_pool = pool
    syncs = {'forced':0,'ordinary':0};lock = threading.Lock();sync = net.progress.sync
    def observed(slug, **kwargs):
        with lock:syncs['forced' if kwargs.get('force') else 'ordinary'] += 1
        return sync(slug, **kwargs)
    net.progress.sync = observed
    def work(fn):
        search = deep_permuter.Search(net, 10, variants*2, log, fn);search.compile_pool = pool
        name = fn['name']
        sources = [f'int {name}(int x){{return x+{i+2};}}' for i in range(variants)]
        scores = []
        with deep_throughput.scope(pool), compile_sessions.session(), trial_bookkeeping.scope(maximum=True):
            chunks = (sources[i:i+slots*2] for i in range(0,len(sources),slots*2))
            for source in search._stream_variants(chunks, agent, proj, fn, None):
                with trial({'kind':'random rewrite','family':'benchmark'}):
                    score = search._score(agent, proj, fn, source, 1, None)
                if score is None or score <= 0:raise RuntimeError('Synthetic miss did not reach the ordinary judge.')
                scores.append(score)
        return scores
    started = time.monotonic()
    try:
        with compiler._SLOTS.capacity(slots), ThreadPoolExecutor(workers) as lanes:
            results = list(lanes.map(work, rows))
        seconds = time.monotonic()-started
        metrics = pool.metrics.snapshot()
        history = net.store.one('SELECT COUNT(*) AS n FROM reuse_trials')['n']
        return dict(functions=functions, variants=variants, workers=workers, slots=slots,
                    header_bytes=len(header.encode()), seconds=seconds,
                    compiler_invocations=metrics['pool_compiler_invocations'],
                    compiles_per_second=metrics['pool_compiler_invocations']/seconds,
                    history_rows=history, scores_sha256=hashlib.sha256(json.dumps(results).encode()).hexdigest(),
                    synchronization_calls=syncs, throughput=metrics)
    finally:
        pool.close()
        net.store.conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--functions', type=int, default=16)
    parser.add_argument('--variants', type=int, default=8)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--slots', type=int, default=4)
    parser.add_argument('--declarations', type=int, default=3000)
    args = parser.parse_args()
    if not (1<=args.functions<=256 and 1<=args.variants<=256 and 1<=args.workers<=256
            and 1<=args.slots<=256 and 0<=args.declarations<=10000):
        parser.error('Use 1–256 functions, variants, workers and slots; 0–10000 declarations.')
    if not toolchains.installed('gcc-2.7.2-kmc'):
        parser.error('Install the pinned gcc-2.7.2-kmc compiler first; this tool never downloads it.')
    with tempfile.TemporaryDirectory(prefix='deep-pipeline-benchmark-') as scratch:
        print(json.dumps(benchmark(Path(scratch), **vars(args)), indent=2))


if __name__ == '__main__':main()
