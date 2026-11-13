#!/usr/bin/env python3
"""Select independent target builds without sharing compiler caches or generated assets."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--variant')
    choice.add_argument('--all-variants', action='store_true')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    targets = json.loads((root / 'variants.json').read_text(encoding='utf-8'))['variants']
    if args.variant and args.variant not in targets:
        parser.error('Unknown variant. Choose: ' + ', '.join(sorted(targets)))
    selected = [args.variant] if args.variant else sorted(targets)
    failed = False
    for variant in selected:
        folder = (root / targets[variant]['path']).resolve()
        if not folder.is_relative_to(root / 'variants'):
            parser.error('Variant path must stay inside variants/.')
        print(f'Building variant {variant}', flush=True)
        result = subprocess.run([sys.executable, 'build.py', *args.command], cwd=folder)
        failed |= result.returncode != 0
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
