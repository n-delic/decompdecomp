"""Cheap checkout-only frontend revision snapshots; no Python module reloading."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from . import edition


def enabled():
    return edition.DEV and os.environ.get('DECOMP_LIVE_RELOAD', '1') != '0'


def snapshot(root: Path | None = None):
    root = root or Path(__file__).with_name('static')
    groups = {'css': [], 'page': []}
    for path in sorted(root.rglob('*')):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue  # Editors can atomically replace a file during a scan.
        groups['css' if path.suffix == '.css' else 'page'].append(
            (path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return {key: hashlib.sha256(repr(value).encode()).hexdigest() for key, value in groups.items()}
