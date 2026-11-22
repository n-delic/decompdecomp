"""Exact judging of many candidates at once: a few `decomp judge-worker` processes beside the app.

The worker is the judge the app itself uses (hosted_judge.judge -> RomNetwork._judge_with: compile with the
function's compiler, link at its address, compare bytes), so a score here is the score the app would give. It opens
the project attach-only and records nothing, so a pool never changes saved state. What it does not do is adopt a
candidate: whoever finds a better score re-judges that one source through the ordinary path, which saves it.

A search that wants thousands of judgments (a two-move beam) cannot wait for the app's one-at-a-time attempt route;
the pool runs about 15 judgments a second per worker.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from . import edition

WORKERS = 4  # beside the app's own compile pool: enough to be fast, few enough to leave the machine usable
LAUNCH = ('import sys\nfrom decompnet.app.cli import main\nsys.exit(main(sys.argv[1:]))')


class Worker:
    """One judge-worker process answering one request at a time."""

    def __init__(self, env):
        self.env = env
        self.proc = None
        self.lock = threading.Lock()

    def _start(self):
        self.proc = subprocess.Popen([sys.executable, '-c', LAUNCH, 'judge-worker'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=self.env)

    def ask(self, request):
        """The reply to one request, or None when the worker died twice."""
        with self.lock:
            for _ in range(2):
                if self.proc is None or self.proc.poll() is not None:
                    self._start()
                try:
                    self.proc.stdin.write(json.dumps(request) + '\n')
                    self.proc.stdin.flush()
                    line = self.proc.stdout.readline()
                    if line:
                        return json.loads(line)
                except (OSError, ValueError):
                    pass
                self.close()
            return None

    def close(self):
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


class JudgePool:
    """`judge_many(sources)` -> scores (0 is a match; None when a source did not compile or could not be judged)."""

    def __init__(self, slug, fn, toolchain, flags, size=WORKERS, home=None):
        target = bytes(fn['target'] or b'').hex()
        self.request = {'op': 'judge', 'slug': slug,
                        'function': {'name': fn['name'], 'vram': fn['vram'], 'target': target},
                        'compiler': {'toolchain': toolchain, 'flags': list(flags)}}
        env = dict(os.environ, DECOMP_HOME=str(home or edition.home_dir()), DECOMP_WINDOW='0', DECOMP_TRAY='0')
        self.workers = queue.Queue()
        self.all = [Worker(env) for _ in range(max(1, size))]
        for worker in self.all:
            self.workers.put(worker)
        self.errors = []

    def judge(self, source):
        worker = self.workers.get()
        try:
            reply = worker.ask(dict(self.request, source=source))
        finally:
            self.workers.put(worker)
        if reply is None:
            return None
        if 'error' in reply:
            self.errors.append(reply['error'])
            return None
        return reply.get('score')

    def judge_rows(self, source):
        """(score, rows) of one source: the exact score and every row of the judge's diff, (marker, target,
        candidate) each (None, [] when it did not compile or could not be judged)."""
        worker = self.workers.get()
        try:
            reply = worker.ask(dict(self.request, source=source, raw_rows=True))
        finally:
            self.workers.put(worker)
        if reply is None:
            return None, []
        if 'error' in reply:
            self.errors.append(reply['error'])
            return None, []
        return reply.get('score'), [tuple(row) for row in reply.get('rows') or []]

    def judge_rows_many(self, sources, check=lambda: None, chunk=64):
        """[(score, rows)] in the order of `sources` (judge_rows for each; `check` runs between chunks)."""
        out = []
        with ThreadPoolExecutor(max_workers=len(self.all)) as executor:
            for at in range(0, len(sources), chunk):
                check()
                out += list(executor.map(self.judge_rows, sources[at:at + chunk]))
        return out

    def judge_many(self, sources, check=lambda: None, chunk=64):
        """Scores in the order of `sources`. `check` runs between chunks (cancellation, deadline)."""
        scores = []
        with ThreadPoolExecutor(max_workers=len(self.all)) as executor:
            for at in range(0, len(sources), chunk):
                check()
                scores += list(executor.map(self.judge, sources[at:at + chunk]))
        return scores

    def close(self):
        for worker in self.all:
            worker.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
