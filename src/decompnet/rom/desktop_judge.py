"""Talk to the Rust ROM judge from a local desktop attempt worker."""

import atexit
import hashlib
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from queue import LifoQueue

from .. import scoring
from ..app import mcp_gateway
from ..hostenv import NO_WINDOW
from . import verifier_link


def _identity(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class RustJudge:
    def __init__(self, program=None):
        self.program = program
        self.process = None
        self.project_key = None
        self.available = True

    def close(self):
        process, self.process = self.process, None
        self.project_key = None
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()

    def _write(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _request(self, action, request, lookup=None):
        self._write({"action": action, "request": request})
        while True:
            reply = json.loads(self.process.stdout.readline())
            if "rom_lookup" not in reply or lookup is None:
                return reply["result"]
            try:
                value = lookup(reply["rom_lookup"])
            except Exception:
                # End the in-flight request before retrying in Python, which reports the reader's error.
                self._write({"error": "desktop ROM lookup failed"})
            else:
                self._write({"rom_value": value})

    def _exchange(self, action, request, lookup=None):
        if not self.available:
            return None
        try:
            if self.process is None:
                program = self.program or mcp_gateway.program()
                if program is None:
                    self.available = False
                    return None
                self.process = subprocess.Popen(
                    [str(program.resolve()), "desktop-judge"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    creationflags=NO_WINDOW, encoding="utf-8",
                )
                if self._request("ping", {}) != 1:
                    self.available = False
                    self.close()
                    return None
        except (OSError, ValueError, KeyError):
            self.available = False
            self.close()
            return None
        try:
            return self._request(action, request, lookup)
        except KeyError:  # Rust declined this input. The Python judge can handle it.
            return None
        except (OSError, ValueError):
            self.close()
            return None

    def link(self, obj, target, symbols, read):
        if not self.available:
            return None
        if not isinstance(read, verifier_link.PackReader):
            def exchange(action, request, lookup):
                return self._exchange(action, {**request, "callback_reader": True}, lookup)

            return verifier_link.link_rom(obj, target, symbols, read, exchange)
        try:
            inputs = read.inputs(symbols)
            root = Path(inputs["root"])
            manifest = root / "memory_regions.json"
            manifest_id = _identity(manifest)
            payload_id = _identity(root / json.loads(manifest.read_bytes())["file"]) if manifest_id else None
            identities = _identity(root / inputs["image"]), manifest_id, payload_id
            key = hashlib.sha256(json.dumps((inputs, identities), sort_keys=True).encode()).hexdigest()
        except (OSError, ValueError, KeyError, TypeError):
            return None

        def exchange(action, request, _lookup):
            request["project_key"] = key
            if key != self.project_key:
                request["project_inputs"] = inputs
            result = self._exchange(action, request)
            if result is not None:
                self.project_key = key
            return result

        return verifier_link.link_rom(obj, target, symbols, read, exchange)

    def data(self, plan, lookup):
        return self._exchange("rom_data_mips", plan, lookup)

    def link_overlay(self, obj, name, target, info):
        return verifier_link.link_overlay(obj, name, target, info, self._exchange)

    def diff(self, target, candidate, *, details=True):
        if not scoring.native_mips_comparison(target, candidate):
            return None
        result = verifier_link.compare_mips(target, candidate, scoring.ALIGN_BRANCH_TARGETS, self._exchange,
                                           details=details, summary=not details)
        if result is None or "rows" not in result:
            return None
        result["rows"] = [tuple(row) for row in result["rows"]]
        return scoring.DiffResult(**result)


# Direct checks can run on search and export threads outside the attempt-worker pool.
# Bound the number of live processes and retain their project snapshots between checks.
_direct_slots = LifoQueue()
_direct_judges = [RustJudge() for _ in range(4)]
for _judge in _direct_judges:
    _direct_slots.put(_judge)
    atexit.register(_judge.close)


@contextmanager
def direct_judge():
    program = mcp_gateway.program()
    if program is None:
        yield None
        return
    judge = _direct_slots.get()
    try:
        if judge.program != program:
            judge.close()
            judge.program = program
            judge.available = True
        yield judge
    except BaseException:
        judge.close()
        raise
    finally:
        _direct_slots.put(judge)


def direct_link(obj, target, symbols, read):
    with direct_judge() as judge:
        return judge.link(obj, target, symbols, read) if judge is not None else None


def direct_overlay(obj, name, code, info):
    with direct_judge() as judge:
        return judge.link_overlay(obj, name, code, info) if judge is not None else None


def direct_diff(target, candidate, *, details=True):
    with direct_judge() as judge:
        return judge.diff(target, candidate, details=details) if judge is not None else None


def direct_evidence(target, candidate, align_branches):
    with direct_judge() as judge:
        if judge is None:
            return None
        return verifier_link.compare_mips(target, candidate, align_branches, judge._exchange)
