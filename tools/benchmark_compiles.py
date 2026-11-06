"""Measure installed pinned compilers with synthetic C in scratch storage, never a user project."""
import argparse
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

from decompnet import compile_sessions, compile_timing, compiler, toolchains
from decompnet.project import compiler_config


def benchmark(cfg, count, workers, prepared, repeat_every=0):
    before = compile_timing.snapshot()
    start = time.monotonic()
    def work(lane):
        done = 0
        with compile_sessions.session() if prepared else nullcontext():
            with compiler.cancellable(lambda: None):
                for i in range(lane, count, workers):
                    value = i % repeat_every if repeat_every else i
                    source = f'int entry(int x) {{ return (x * 3) + {value}; }}'
                    result = compiler.compile_source(cfg, 'typedef unsigned int u32;\n', source)
                    if not result.ok:raise RuntimeError(result.log)
                    done += 1
        return done
    with ThreadPoolExecutor(workers) as pool:
        done = sum(pool.map(work, range(workers)))
    seconds = time.monotonic() - start
    after = compile_timing.snapshot()
    return dict(workers=workers, prepared=prepared, count=done, seconds=round(seconds, 4),
                candidates_per_second=round(done / seconds, 2),
                phases={k:round(after[k]-before.get(k, 0), 4) for k in after})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', required=True)
    parser.add_argument('--count', type=int, default=64)
    parser.add_argument('--workers', default='1,2,4,8')
    parser.add_argument('--flags', default='-O2')
    parser.add_argument('--repeat-every', type=int, default=0, help='Optional duplicate candidate cycle; zero tests unique candidates.')
    args = parser.parse_args()
    workers = [int(n) for n in args.workers.split(',')]
    if not 0 <= args.repeat_every <= 10000 or not 1 <= args.count <= 10000 or not workers or any(not 1 <= n <= 256 for n in workers):
        parser.error('Use 1–10000 candidates and 1–256 workers.')
    if not toolchains.installed(args.toolchain):
        parser.error('Install this compiler through Compilers & tools first. This benchmark never downloads tools.')
    with tempfile.TemporaryDirectory(prefix='decomp-compile-benchmark-') as folder:
        cfg = compiler_config(Path(folder), {'compiler':{'toolchain':args.toolchain, 'flags':args.flags.split()}})
        cfg.identity = ('synthetic compile benchmark',)
        rows = [benchmark(cfg, args.count, n, prepared, args.repeat_every) for n in workers for prepared in (False, True)]
    print(json.dumps({'compiler_slots':compiler.concurrency_limit(), 'results':rows,
                      'recommended_workers':max((r for r in rows if r['prepared']), key=lambda r:r['candidates_per_second'])['workers']}, indent=2))


if __name__ == '__main__':main()
