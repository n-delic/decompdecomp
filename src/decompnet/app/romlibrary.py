"""Read-only, bounded hash lookup for games used by imported public projects."""

from __future__ import annotations

import hashlib
import os
import re
import time
from functools import lru_cache
from pathlib import Path

from .. import consoles

MAX_FILES = 5000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_SECONDS = 30
ARCHIVES = {'.zip', '.7z', '.rar', '.tar', '.gz', '.bz2', '.xz'}
# Windows ctime is creation time and may not change after a same-size overwrite.
CACHE_HASHES = os.name != 'nt'


@lru_cache(maxsize=10000)
def _hash(path: str, size: int, mtime: int, ctime: int, platform: str) -> str:
    if not platform:
        from .forkroom import game_identity

        return game_identity(Path(path))
    if platform != 'raw':
        if identity := consoles.get(platform).identity(Path(path)):
            return identity
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha1').hexdigest()


def fingerprint(path: Path, platform: str) -> str | None:
    """Hash a stable regular file using its console's game identity when available."""
    try:
        stat = path.stat()
        if not path.is_file():
            return None
        hasher = _hash if CACHE_HASHES else _hash.__wrapped__
        value = hasher(str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, platform)
        after = path.stat()
        if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            return None
        return value
    except (OSError, ValueError):
        return None


def _candidate(path: Path, platform: str) -> int:
    """Return a file's size when it is safe and plausible to hash, or zero to skip it."""
    if path.is_symlink() or path.suffix.lower() in ARCHIVES:
        return 0
    try:
        size = path.stat().st_size
        if not 0 < size <= MAX_FILE_BYTES:
            return 0
        if platform:
            if not consoles.get(platform).library_candidate(path, size):
                return 0
        else:
            # A repository pasted by URL can have no platform metadata. Ask consoles to identify the file
            # before applying their size and format checks, rather than guessing from its extension.
            with path.open('rb') as stream:
                head = stream.read(consoles.HEAD_BYTES)
            for console in consoles.startable():
                if console.sniff(head, path) and not console.library_candidate(path, size):
                    return 0
        return size
    except (OSError, ValueError):
        return 0


def find(project, needs: dict, folder: str, log=print) -> dict:
    """Find an exact SHA-1 match in a local folder within fixed file, byte and time budgets."""
    expected = str(needs.get('sha1') or '').lower()
    if not re.fullmatch('[0-9a-f]{40}', expected):
        return {'path': None, 'reason': 'This project does not declare an expected game SHA-1.'}
    platform = str(getattr(project, 'platform', '') or '')
    local = project.root / needs['path'] if needs.get('path') else None
    # Retry setup without copying only when the file already has the exact bytes the project expects.
    if local and fingerprint(local, 'raw') == expected:
        return {'path': str(local), 'in_project': True}
    if not folder:
        return {'path': None, 'reason': 'Set a ROM library folder in Settings to find your game.'}
    root = Path(folder).expanduser()
    if not root.is_dir():
        return {'path': None, 'reason': 'The ROM library folder is unavailable. Check its path in Settings.'}

    log('Searching the ROM library and its subfolders for the expected SHA-1…')
    count = skipped = hashed = 0
    started = time.monotonic()

    def inaccessible(error):
        nonlocal skipped
        skipped += 1

    for parent, dirs, files in os.walk(root, followlinks=False, onerror=inaccessible):
        dirs[:] = sorted(d for d in dirs if not (Path(parent, d).is_symlink() or os.path.isjunction(Path(parent, d))))
        for name in sorted(files):
            if count >= MAX_FILES or hashed >= MAX_TOTAL_BYTES or time.monotonic() - started >= MAX_SECONDS:
                return {'path': None, 'scanned': count, 'skipped': skipped,
                        'reason': f'Search stopped after {count} files and {hashed} bytes. Choose the file manually '
                                  'or use a smaller ROM library folder.'}
            path = Path(parent, name)
            size = _candidate(path, platform)
            if not size:
                skipped += 1
                continue
            if hashed + size > MAX_TOTAL_BYTES:
                return {'path': None, 'scanned': count, 'skipped': skipped,
                        'reason': 'Search reached its 2 GiB read limit. Choose the file manually or use a smaller folder.'}
            count += 1
            hashed += size
            if fingerprint(path, platform) == expected:
                log(f'Found a matching game: {path}')
                return {'path': str(path), 'in_project': False, 'scanned': count, 'skipped': skipped}
            if count % 100 == 0:
                log(f'Checked {count} files…')
    return {'path': None, 'scanned': count, 'skipped': skipped,
            'reason': f'No matching game found in {count} files. {skipped} files or folders were skipped. '
                      'Choose a file manually if it is archived or outside this folder.'}
