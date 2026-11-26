"""Does a garbage collector setting help a long-running, many-threaded server like the app? Synthetic data only.

    uv run python tools/gc_benchmark.py                      # every setting, each in a fresh interpreter
    uv run python tools/gc_benchmark.py --live 1500000       # a bigger long-lived heap

The shape: a long-lived heap of cached rows, threads that each build and drop request-sized batches of dicts, tuples
and parsed JSON, a bounded cache whose entries survive a while and then die (what drives full collections), and
failed calls that leave a reference cycle holding a large string (what only the collector frees). It reports time in
the collector, the longest pause and peak memory; compare settings on one machine, not numbers across machines.

No setting is applied in the app. Raised thresholds cut the time here by about half, but peak memory then grows with
the length of the run (--requests 900 or 2400): collections are counted in objects, so cycles holding large strings
wait far longer to be freed, and nothing bounds their bytes. `gc.freeze()` alone did not help. Measure long runs.
"""
import argparse
import gc
import json
import re
import resource
import subprocess
import sys
import threading
import time
from collections import OrderedDict

SETTINGS = {'default': (None, False), 'freeze': (None, True),
            '2000': ((2_000, 10, 10), False), '5000': ((5_000, 10, 10), False),
            '10000': ((10_000, 10, 10), False), '10000+freeze': ((10_000, 10, 10), True),
            '50000': ((50_000, 10, 10), False), '50000+freeze': ((50_000, 10, 10), True),
            '50000,20,20': ((50_000, 20, 20), False)}
PROTOTYPE = re.compile(r'(\w+)\s+(\w+)\s*\(([^()]*)\)\s*;')


def rows(start, count):
    return [{'id': i, 'name': f'func_{i:08X}', 'span': (i, i + 64), 'tags': [i & 7, 'open']}
            for i in range(start, start + count)]


def failed_call(n):
    """An exception kept in a local makes a cycle (frame -> exception -> traceback -> frame)."""
    held = 'x' * 200_000
    try:
        raise ValueError(n)
    except ValueError as error:
        kept = error  # noqa: F841  (the cycle is the point)
    return len(held)


def worker(number, requests, cache, lock):
    header = ''.join(f'int helper_{i}(int a, short b);\n' for i in range(200))
    for n in range(requests):
        batch = rows(number * 1_000_000 + n * 600, 600)
        parsed = json.loads(json.dumps(batch))
        matches = [m.groups() for m in PROTOTYPE.finditer(header)]
        index = {row['name']: (row['id'], tuple(row['tags'])) for row in parsed}
        if n % 4 == 0:
            with lock:
                cache[number, n] = (batch, index, matches)
                while len(cache) > 128:
                    cache.popitem(last=False)
        if n % 25 == 0:
            failed_call(n)


def run(setting, live, threads, requests):
    thresholds, freeze = SETTINGS[setting]
    startup = rows(0, 100_000)  # what importing the app leaves behind
    if freeze:
        gc.collect()
        gc.freeze()
    if thresholds:
        gc.set_threshold(*thresholds)
    pauses, began, counts = [], [0.], [0, 0, 0]

    def seen(phase, info):
        if phase == 'start':
            began[0] = time.perf_counter()
        else:
            pauses.append((time.perf_counter() - began[0], info['generation']))
            counts[info['generation']] += 1
    gc.callbacks.append(seen)
    heap = rows(10_000_000, live // 3)  # a row is three tracked containers or so
    cache, lock = OrderedDict(), threading.Lock()
    built = sum(p for p, _ in pauses)
    wall, cpu = time.perf_counter(), time.process_time()
    workers = [threading.Thread(target=worker, args=(i, requests, cache, lock)) for i in range(threads)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
    collecting = sum(p for p, _ in pauses) - built
    scale = 1 if sys.platform == 'darwin' else 1024
    print(json.dumps({'setting': setting, 'wall': wall, 'cpu': cpu, 'gc': collecting,
                      'gc_share': collecting / wall, 'max_pause_ms': 1000 * max((p for p, _ in pauses), default=0),
                      'collections': counts, 'peak_mb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale / 2**20,
                      'kept': len(startup) + len(heap)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--setting', choices=SETTINGS)
    parser.add_argument('--live', type=int, default=600_000, help='long-lived tracked objects')
    parser.add_argument('--threads', type=int, default=24)
    parser.add_argument('--requests', type=int, default=150, help='per thread')
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if args.setting:
        run(args.setting, args.live, args.threads, args.requests)
        return
    print(f'Python {sys.version.split()[0]}, live={args.live} threads={args.threads} requests={args.requests}, '
          f'median of {args.repeat}')
    print(f"{'setting':14} {'wall s':>7} {'cpu s':>7} {'gc s':>6} {'gc %':>5} {'max ms':>7} {'peak MB':>8}  collections")
    for setting in SETTINGS:
        results = [json.loads(subprocess.run(
            [sys.executable, __file__, '--setting', setting, '--live', str(args.live), '--threads', str(args.threads),
             '--requests', str(args.requests)], check=True, capture_output=True, text=True).stdout)
            for _ in range(args.repeat)]
        r = sorted(results, key=lambda item: item['wall'])[len(results) // 2]
        print(f"{setting:14} {r['wall']:7.2f} {r['cpu']:7.2f} {r['gc']:6.2f} {100 * r['gc_share']:5.1f} "
              f"{r['max_pause_ms']:7.0f} {r['peak_mb']:8.0f}  {r['collections']}")


if __name__ == '__main__':
    main()
