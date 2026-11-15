"""Runs luvdis (in its own environment: `python luvdis_run.py disasm ...`) with fixes to its emulation of the stack.

luvdis models the stack as a list indexed by the number of words pushed. Code that pops more than it pushed (a
function entered past its own push, or a guess that isn't a function) takes that index below zero, and the next push
then writes outside the list: "IndexError: list assignment index out of range" ended a GBA game's whole
disassembly. A push there now stores nothing, as a load from there already reads "unknown".

Its `add sp, #imm` extends the list by `len(stack) - sp`, but that instruction takes `sp` below zero in its model,
so every one roughly doubles the list. Data that decodes as a run of `add sp` (a 16-32 MiB cartridge's graphics
and sound) grew it until luvdis died with MemoryError (#187). It now only grows to the new depth and never past a
16 KiB frame, which is more than any GBA function allocates.

Standard library only: this runs in luvdis's environment, not decompdecomp's.
"""

import importlib.util
import sys

STACK_WORDS = 0x1000  # 16 KiB: the GBA's whole stack is 32 KiB, so no real function frame reaches this

PUSH = """                        if self.sp >= len(self.stack):
                            self.stack.append(value)
                        else:
                            self.stack[self.sp] = value
"""
SAFE_PUSH = f"""                        if self.sp >= len(self.stack):
                            if len(self.stack) < {STACK_WORDS}:
                                self.stack.append(value)
                        elif self.sp >= 0:
                            self.stack[self.sp] = value
"""
THUMB13 = """                self.sp -= offset
                for _ in range(len(self.stack)-self.sp):
                    self.stack.append(self.unknown)
"""
SAFE_THUMB13 = f"""                self.sp -= offset
                grow = min(self.sp, {STACK_WORDS}) - len(self.stack)
                if grow > 0:
                    self.stack.extend([self.unknown] * grow)
"""


def main() -> int:
    spec = importlib.util.find_spec("luvdis.analyze")
    with open(spec.origin, encoding="utf-8") as f:
        source = f.read()
    module = importlib.util.module_from_spec(spec)
    sys.modules["luvdis.analyze"] = module
    exec(compile(source.replace(PUSH, SAFE_PUSH, 1).replace(THUMB13, SAFE_THUMB13, 1), spec.origin, "exec"),
         module.__dict__)
    from luvdis.__main__ import main as luvdis

    return luvdis(prog_name="luvdis")


if __name__ == "__main__":
    sys.exit(main())
