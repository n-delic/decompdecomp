"""n64_record_seeds.py ROM ADDRESS --input SCRIPT --vis N --out FILE: the seeds a game's random-number generator is given on the console.

A game that seeds from the clock (`rand_seed(osGetTime())`) draws different numbers in the port, whose clock is not the console's
cycle count, so a camera cut or a spawn that depends on a draw differs from the emulator's whatever else is exact. The emulator's
break on the seeding function (its address: `fixed.txt`, or the project's symbol) reports its first argument, the seed, each time
it is called; this writes them, one a line in call order, for the port's PORT_SEED_REPLAY. A port-only override of the seeding
function (port/wasm/src/, which the build prefers to the matched C) passes its seed through `port_seed_replay` and gets the next
of these while there are any. A racing game's 2,391 calls through its first 7,142 retraces came back in the order the port made
them (the port's call 1,565 was the console's first chase-camera seed), and with them replayed the race matched the emulator at
22 of 23 windows."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def seeds(hits) -> list[int]:
    """The first argument of each break hit, as the unsigned 32-bit number the function was given."""
    return [int(h.args[0]) & 0xFFFFFFFF for h in hits]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Record the seeds the console's game gives its random-number generator.")
    p.add_argument("rom", type=Path)
    p.add_argument("address", help="the seeding function's address (hex)")
    p.add_argument("--input", default="", help="the input script (vi:BUTTON,... or @file)")
    p.add_argument("--input-clock", choices=("vi", "frames"), default="frames")
    p.add_argument("--vis", type=int, required=True, help="how many retraces to run")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    from decompnet.consoles.n64 import emulator
    script = Path(args.input[1:]).read_text().strip() if args.input.startswith("@") else args.input
    run = emulator.run(args.rom, args.vis, inputs=script, input_clock=args.input_clock, breaks=[int(args.address, 16)],
                       max_hits=1_000_000)
    found = seeds(run.hits)
    args.out.write_text("".join(f"{s}\n" for s in found))
    print(f"{len(found)} seeds written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
