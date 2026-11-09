"""Stamp installed packages with the exact source commit, independently of local install metadata."""
import json
import subprocess
import sys
from pathlib import Path


def stamp(root, package):
    sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Packaging needs a valid source commit.')
    dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'], text=True).strip())
    if dirty:
        raise ValueError('Commit source changes before packaging so its build ID identifies the shipped code.')
    (package / 'build-info.json').write_text(json.dumps({'fork': 'RetroReversing', 'commit': sha})+'\n')


if __name__ == '__main__':
    if len(sys.argv) > 2:
        package = Path(sys.argv[2]) / 'decompnet'
    else:
        import decompnet
        package = Path(decompnet.__file__).parent
    stamp(Path(sys.argv[1]), package)
