"""ROM-mode ARM consoles share per-function modes, targets and the byte-for-byte judge.

Splitting and reading a game belong to the console; ARM versus Thumb belongs to each function.
"""

from __future__ import annotations

import json

from . import Console


class ArmConsole(Console):
    def function_insns(self, fn) -> int:
        return fn.n_insns if hasattr(fn, "n_insns") else fn.get("n_insns") or 1

    def function_asm(self, fn) -> str:
        return fn.asm if hasattr(fn, "asm") else fn["asm"]

    def function_meta(self, fn) -> dict:
        """The mode a function's row keeps: the judge and twins need Thumb or ARM per function, not per console."""
        row = dict(fn) if not hasattr(fn, "thumb") else {}
        meta = json.loads(row.get("meta") or "{}")
        return {"thumb": fn.thumb if hasattr(fn, "thumb") else meta.get("thumb", "arm_func_start" not in (row.get("asm") or ""))}

    def function_status(self, fn) -> str:
        return "asm" if (fn.asm_only if hasattr(fn, "asm_only") else False) else "open"

    def instruction_size(self, fn) -> int:
        return 2 if self.function_meta(fn)["thumb"] else 4

    def target_from_split(self, fn, resolve, big_endian: bool, gp: int):
        from ..arch.arm import ArmTarget

        return ArmTarget(fn.name, fn.vram, fn.code, fn.thumb, fn.refs or [])

    def target_from_row(self, fn: dict, cfg: dict):
        from ..arch.arm import ArmTarget

        meta = json.loads(fn["meta"] or "{}")
        return ArmTarget(fn["name"], fn["vram"], fn["target"], meta.get("thumb", True),
                         ArmTarget.relocs_from_json(json.loads(fn["relocs"] or "[]")))

    def target_object(self, fn: dict, cfg: dict, symbols):
        from ..arch.arm import canonical_target

        return canonical_target(self.target_from_row(fn, cfg))

    def target_arch(self, fn) -> str:
        if hasattr(fn, "thumb"):
            return "thumb" if fn.thumb else "arm"
        meta = json.loads((fn.get("meta") if isinstance(fn, dict) else "") or "{}")
        return "thumb" if meta.get("thumb", True) else "arm"

    def link_function(self, obj: bytes, name: str, target, symbols, read):
        from ..arch import arm

        return arm.link(obj, name, target, symbols, read)
