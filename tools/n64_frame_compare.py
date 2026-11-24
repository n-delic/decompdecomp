"""Compare an N64 port's frames with the original game's, run in the reversing emulator with the same input.

The reversing emulator (mupenRE, consoles/n64/emulator) runs the user's own ROM headless but draws nothing: its video
plugin only finishes each display list. What it can do is write the console's RAM at chosen retraces, and the port
writes its own the same way (PORT_RAM_DUMPS). Each dump holds the game's graphics tasks (libultra's OSTask, the
display list and the microcode's data it names); this draws them with the port's renderer
(src/decompnet/consoles/n64/port_wasm/render.c, which matches angrylion's RDP: tools/n64_rdp_compare.py), so the
console's frame and the port's are drawn by the same renderer from each one's own game state. Where they differ,
the game ran differently (the port's code, its host or its input), not the renderer.

    uv run python tools/n64_frame_compare.py ROM --port path/to/port/build/game --frames 900,1200 \
        --input @script.txt --out REPORT

--frames compares the game's own frames: the nth graphics task it hands over, counted the same way on both sides
(the emulator's video plugin, the port's host), with the RAM written as that task is handed over (its display list
finished, nothing half built) and the input script timed in frames too (`frame:BUTTON+BUTTON+SX=n+SY=n,...`). It
needs nothing from the game. What it can't do is make the two runs one: the console drops frames and spends
retraces loading, the port does neither, so a game that times its menus and animations by retraces is further on
per frame on the console (a racing game: 5,023 retraces by its 2,300th frame on the console, 4,710 in the port), and
a press lands on another screen. Compare where the game waits for the player, choose points from a first run's
images, or give the port its own script (--port-input).

--vis compares at retraces instead (PORT_RAM_DUMPS and PORT_INPUT_CLOCK=vi on the port's side), with --port-vis to
pair each of the console's with the port's at the same moment and --port-input to give the port its own script.
Without --port only the console's frames are drawn. For each point and each graphics task found in RAM, it writes
the console's image, the port's and their difference (brightened) side by side, and prints how many pixels differ.

A game builds its next display list while the last one is drawn, so a dump taken at a retrace may hold one half
built: it may run into old data and fail to draw, which is reported and skipped.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
# the engine is the app's (Check pictures in the Porting tab runs it too); the names stay here for the callers and tests
from decompnet.port.frame_compare import (  # noqa: E402,F401
    HARNESS,
    HEADER,
    RAM,
    RENDER,
    TASK_AT,
    Renderer,
    build,
    commands,
    compare,
    console_save,
    difference,
    draw,
    frames,
    graphics_tasks,
    images,
    png,
    prime_save,
    retraces_per_second,
    rgb,
    run_console,
    run_port,
    side_by_side,
    unmatched,
    with_task,
)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Compare an N64 port's frames with the original game's, run in the "
                                            "reversing emulator with the same input.")
    p.add_argument("rom", type=Path)
    p.add_argument("--port", type=Path, help="the port's native build (port/wasm/build/game); without it, only the "
                                             "console's frames")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--frames", help="the game's frames to compare (its graphics tasks, counted from 1), "
                                       "comma-separated, each n or first-last: the input script is timed in frames too")
    when.add_argument("--vis", help="retraces to compare at, comma-separated: the input script is timed in retraces")
    p.add_argument("--port-vis", help="with --vis, the port's retraces to compare with each, when the two runs drift "
                                      "apart (the port loads in no time and never drops a frame): the same count")
    p.add_argument("--input", default="", help="vi:BUTTON+BUTTON+SX=n+SY=n,... or @file")
    p.add_argument("--port-input", help="the port's own script (default: --input's)")
    p.add_argument("--prime-save", action="store_true",
                   help="run the port once through the input script first, so the compared run starts from the save that "
                        "run wrote (game.eep in --work): a port started with no save diverged from the emulator's race")
    p.add_argument("--console-save", action="store_true",
                   help="start the port from the console's own save: the game's copy of its EEPROM is found in the console's RAM "
                        "at --save-at (after a run of the script that gives the port a save to look for) and written as the "
                        "port's game.eep; a port that starts from nothing boots differently from the emulator")
    p.add_argument("--save-at", type=int, default=600, help="with --console-save, the frame (or retrace) of the console RAM to "
                   "take the save from, after the game has read it (default 600)")
    p.add_argument("--tolerance", type=int, default=0, metavar="PIXELS",
                   help="also report the share of pixels with no equal pixel within PIXELS of the other side's picture "
                        "(a moving scene the two runs reach at slightly different moments)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--work", type=Path, help="where the renderer is built and the dumps kept (default: a temporary "
                                             "folder)")
    args = p.parse_args(argv)
    clock = "frames" if args.frames else "vi"
    points = []
    for v in (args.frames or args.vis).split(","):  # "n" or "first-last" (a run of frames: only some of a game's
        if v.strip():                               # graphics tasks draw the picture, the rest draw into it)
            first, _, last = v.strip().partition("-")
            points += range(int(first), int(last or first) + 1)
    port_points = [int(v) for v in args.port_vis.split(",") if v.strip()] if args.port_vis and args.vis else points
    if len(port_points) != len(points):
        raise SystemExit("--port-vis needs one retrace for each of --vis")
    pairs = dict(zip(points, port_points))
    unit = "frame" if clock == "frames" else "VI"

    def script(text: str) -> str:
        return Path(text[1:]).read_text().strip() if text.startswith("@") else text
    inputs = script(args.input)
    port_inputs = script(args.port_input) if args.port_input is not None else inputs
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="n64-frames-") as tmp:
        work = args.work or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        program = build(work)
        early = args.save_at if args.console_save and args.port else None
        console = run_console(args.rom, sorted(set(points) | ({early} if early else set())), inputs, work, clock)
        if args.port and (args.prime_save or args.console_save) and prime_save(args.port, args.rom, port_inputs, work, clock):
            print("primed the port's save (game.eep) by a run of the input script: see prime_save")
        if early and early in console and (work / "game.eep").exists():
            from_console = console_save(console[early], (work / "game.eep").read_bytes())
            if from_console:
                (work / "game.eep").write_bytes(from_console)
                print(f"the port's save is the console's own, from its RAM at {unit.lower()} {early} (see console_save)")
            else:
                print(f"couldn't find the game's save in the console's RAM at {unit.lower()} {early}: the port keeps its own")
        port = run_port(args.port, args.rom, sorted(set(port_points)), port_inputs, work, clock) if args.port else {}
        for n in points:
            pn = pairs[n]
            label = f"{unit} {n}" + (f" (port {pn})" if pn != n else "")
            if n not in console:
                print(f"{label}: the emulator wrote no RAM (did it run that far?)")
                continue
            ours = frames(program, port[pn], work) if pn in port else {}
            if args.port and pn not in port:
                print(f"{label}: the port wrote no RAM (did it run that far?)")
            theirs = frames(program, console[n], work)
            only_a, only_b = [k for k in theirs if k not in ours], [k for k in ours if k not in theirs]
            if len(only_a) == len(only_b) == 1 and only_a[0][0] == only_b[0][0]:
                # one picture each at different addresses (the console and the port rotate their frame buffers
                # differently): the same picture, kept under the console's address
                ours[only_a[0]] = ours.pop(only_b[0])
            for key in sorted(set(theirs) | set(ours)):
                task, image = key
                a, b = theirs.get(key), ours.get(key)
                name = args.out / (f"{unit.lower()}{n:06d}{f'_port{pn:06d}' if pn != n else ''}"
                                   f"_task{task:08X}_image{image:06X}.png")
                if a and b and a[:2] == b[:2]:
                    c = compare(b[2], a[2])
                    side_by_side(name, [a, b, (a[0], a[1], difference(a[2], b[2]))])
                    near = (f", {unmatched(a[2], b[2], a[0], a[1], args.tolerance):.2f}% with no match within "
                            f"{args.tolerance} px") if args.tolerance else ""
                    print(f"{label} task {task:08X} image {image:06X} {a[0]}x{a[1]}: {c['differ_pct']:.2f}% of "
                          f"pixels differ, {c['beyond_pct']:.2f}% by more than one step{near}")
                else:
                    side_by_side(name, [a, b])
                    where = "the console's" if a and not b else "the port's" if b and not a else "both (sizes differ)"
                    print(f"{label} task {task:08X} image {image:06X}: drawn in {where} only")
        print(f"images in {args.out}: the console's, the port's, their difference (x4)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
