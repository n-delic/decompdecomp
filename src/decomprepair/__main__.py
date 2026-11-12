"""`python -m decomprepair FILE.c --function NAME`: print or write every family's proposals for one function.

It never compiles: compile each written file with the game's own compiler (or hand them to objdiff, the
permuter's base, a decomp.me scratch) and keep only what matches.
"""

import argparse
import json
import pathlib
import sys

from .propose import propose


def main(argv=None):
    parser = argparse.ArgumentParser(prog='decomprepair', description=__doc__.split('\n')[0])
    parser.add_argument('file', type=pathlib.Path, help='the C source that holds the function')
    parser.add_argument('--function', required=True, help="the function's name")
    parser.add_argument('--header', type=pathlib.Path, help='a header with the declarations the source uses')
    parser.add_argument('--asm', type=pathlib.Path, help="the target function's assembly, for families that read it")
    parser.add_argument('--arch', choices=('mips', 'ppc', 'x86', 'arm'), help='the processor (most families: mips)')
    parser.add_argument('--flags', default='', help='the compiler flags, space-separated')
    parser.add_argument('--only', action='append', help='run only this module (repeatable)')
    parser.add_argument('--out', type=pathlib.Path, help='write each proposal to this folder as NNN_family.c')
    parser.add_argument('--json', action='store_true', help='print proposals and timings as JSON')
    parser.add_argument('--timing', action='store_true', help='print how long each family took')
    options = parser.parse_args(argv)
    read = lambda path: path.read_text(encoding='utf-8', errors='replace') if path else None  # noqa: E731
    report = propose(read(options.file), options.function, header=read(options.header) or '', arch=options.arch,
                     asm=read(options.asm), flags=tuple(options.flags.split()) or None, only=options.only)
    if options.json:
        json.dump(dict(proposals=[vars(p) for p in report.proposals], timings=[vars(t) for t in report.timings],
                       ms=report.ms), sys.stdout, indent=1)
        print()
        return 0
    if options.out:
        options.out.mkdir(parents=True, exist_ok=True)
        for index, proposal in enumerate(report.proposals):
            (options.out / f'{index:03}_{proposal.family}.c').write_text(proposal.source, encoding='utf-8')
    ran = [t for t in report.timings if not t.skipped]
    print(f'{len(report.proposals)} proposals from {len(ran)} of {len(report.timings)} families in {report.ms:.1f} ms'
          + (f', written to {options.out}' if options.out else ''))
    for timing in sorted(report.timings, key=lambda t: -t.ms) if options.timing else []:
        state = timing.error or timing.skipped or f'{timing.proposals} proposals'
        print(f'  {timing.ms:8.2f} ms  {timing.module}: {state}')
    errors = [t for t in report.timings if t.error]
    for timing in errors if not options.timing else []:
        print(f'  {timing.module} failed: {timing.error}', file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
