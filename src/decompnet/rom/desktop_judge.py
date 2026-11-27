"""Talk to the Rust ROM judge from a local desktop attempt worker."""

import hashlib
import json
import subprocess
from pathlib import Path

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
    def __init__(self):
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

    def _request(self, action, request):
        self.process.stdin.write(json.dumps({"action": action, "request": request}) + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())["result"]

    def _exchange(self, action, request):
        if not self.available:
            return None
        try:
            if self.process is None:
                program = mcp_gateway.program()
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
            return self._request(action, request)
        except KeyError:  # Rust declined this input. The Python judge can handle it.
            return None
        except (OSError, ValueError):
            self.close()
            return None

    def link(self, obj, target, symbols, read):
        if not self.available:
            return None
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

    def link_overlay(self, obj, name, target, info):
        return verifier_link.link_overlay(obj, name, target, info, self._exchange)

    def diff(self, target, candidate):
        if not scoring.native_mips_comparison(target, candidate):
            return None
        result = verifier_link.compare_mips(target, candidate, scoring.ALIGN_BRANCH_TARGETS, self._exchange, details=True)
        if result is None:
            return None
        result["rows"] = [tuple(row) for row in result["rows"]]
        return scoring.DiffResult(**result)
