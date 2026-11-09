"""User-visible fork identity, including provenance embedded in portable packages."""
import json
import subprocess
from functools import lru_cache
from pathlib import Path

from .. import __version__

FORK = 'RetroReversing fork'


@lru_cache(maxsize=1)
def commit():
    package = Path(__file__).resolve().parents[1]
    try:
        value = json.loads((package / 'build-info.json').read_text())['commit']
        if len(value) == 40 and all(c in '0123456789abcdef' for c in value):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    root = package.parents[1]
    if not (root / '.git').exists():
        return ''
    try:
        return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def label():
    return f'{__version__} · {FORK} · {commit()[:12] or "build unknown"}'
