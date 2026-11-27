"""Compile, link and compare candidates without agent or result-recording access."""
from __future__ import annotations

import mmap
import os
import queue
import sys
import threading
from contextlib import closing
from pathlib import Path

from decompnet import compile_sessions, compile_timing, compiler, consoles
from decompnet.app.engine.core import Core
from decompnet.compile_worker import _read, _write
from decompnet.forks.project import ForkProject
from decompnet.rom import match, split
from decompnet.rom.desktop_judge import RustJudge
from decompnet.rom.verifier_link import PackReader


class RomReader:
    cfg = staticmethod(Core.cfg)
    reader = Core.reader

    def __init__(self):
        self.rom = None
        self.key = None

    def _rom(self, proj):
        path = Path(proj["root"]) / self.cfg(proj)["rom"]["file"]
        stat = path.stat()
        key = (proj["id"], proj["config"], path, stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns)
        if key != self.key:
            segments = [split.Segment(**item) for item in self.cfg(proj)["segments"]]
            with path.open("rb") as stream:
                data = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) if stat.st_size else b""
            self.close()
            self.rom = (data, segments)
            self.key = key
        return self.rom

    def close(self):
        if self.rom is not None and isinstance(self.rom[0], mmap.mmap):
            self.rom[0].close()
        self.rom = self.key = None
        self.__dict__.pop("_images", None)


class ProjectJudge(ForkProject):
    def __init__(self, root, invoke):
        super().__init__(root, create_state=False)
        self.invoke = invoke

    def _execute(self, cmd, log, timeout, failed, platform=None):
        result, misses = self.invoke(cmd, timeout, failed is not None, platform)
        if failed is not None:
            failed.extend(name for name in misses if name not in failed)
        for line in result[1]:
            log(line)
        return result


def perform(reader, judge, build, action, args):
    if action == "compile":
        return compiler.compile_source(*args)
    if action == "rom-link":
        proj, fn, obj, target, symbols = args
        try:
            console = consoles.of(reader.cfg(proj))
            if console.rust_desktop_judge and type(target) is match.RomTarget:
                # Enter through the same linker as Python so its C++ symbol normalization still runs.
                token = match.rom_linker.set(judge.link)
                try:
                    return console.link_function(obj, fn["name"], target, symbols, PackReader(reader, proj, fn))
                finally:
                    match.rom_linker.reset(token)
            return console.link_function(obj, fn["name"], target, symbols, reader.reader(proj, fn))
        finally:
            if os.name == "nt":  # a live mapping would block replacing an image on Windows
                reader.close()
    if action == "score":
        from decompnet.network import score_candidate

        # A running app can reload this worker from a source update.
        target, candidate, verdict, *options = args
        native = options and options[0]
        return score_candidate(target, candidate, verdict, compare=judge.diff if native else None)
    root, *parameters = args
    project = ProjectJudge(root, build)
    if action == "project-build":
        return project.build_units(*parameters)
    if action == "project-diff":
        return project.diff(*parameters)
    if action == "project-verify":
        return project.verify()
    raise ValueError(f"Unknown attempt worker operation {action}")


def serve():
    source, replies = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr
    reader = RomReader()
    judge = RustJudge()
    pending = queue.Queue()
    cancelled = threading.Event()

    def receive():
        try:
            while (message := _read(source)) is not None:
                if message[0] == "cancel":
                    cancelled.set()
                else:
                    pending.put(message)
        finally:
            cancelled.set()
            pending.put(None)

    def check():
        if cancelled.is_set():
            raise RuntimeError("The attempt was cancelled.")

    threading.Thread(target=receive, daemon=True, name="attempt-worker-input").start()

    def build(*args):
        _write(replies, ("build", args))
        reply = pending.get()
        if reply is None:
            raise SystemExit(1)
        kind, value = reply
        if kind == "error":
            raise value
        return value

    _write(replies, ("ready", None))
    with closing(judge), compile_sessions.session():
        while (request := pending.get()) is not None:
            counts = compile_timing.Counts()
            try:
                with compile_timing.collect(counts), compiler.cancellable(check):
                    result = perform(reader, judge, build, *request)
            except Exception as error:
                _write(replies, ("error", (error, counts.values)))
            else:
                _write(replies, ("ok", (result, counts.values)))


if __name__ == "__main__":
    serve()
