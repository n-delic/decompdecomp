"""Release spelling remains valid in Python metadata and every packaged app."""
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('version,pep', [('1.0.9-rr.1', '1.0.9+rr.1'), ('1.0.9-alpha.4', '1.0.9a4'),
                                        ('1.0.9-rc', '1.0.9rc0'), ('1.0.9', '1.0.9')])
def test_release_script_writes_display_and_package_versions(tmp_path, version, pep):
    if not shutil.which('bash') or not shutil.which('python3'):
        pytest.skip('release script requires bash and python3')
    for filename in ('packaging/release.sh', 'pyproject.toml', 'src/decompnet/__init__.py',
                     'site/public/install.sh', 'uv.lock'):
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / filename, target)
    binary = tmp_path / 'bin'
    binary.mkdir()
    git = binary / 'git'
    git.write_text('''#!/bin/sh
case "$1" in
 branch) echo main ;;
 rev-parse|ls-remote) exit 1 ;;
 *) exit 0 ;;
esac
''')
    git.chmod(0o755)
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'])
    subprocess.run(['bash', str(tmp_path / 'packaging/release.sh'), version, 'Fork release'],
                   env=env, check=True, capture_output=True, text=True)
    assert tomllib.loads((tmp_path / 'pyproject.toml').read_text())['project']['version'] == pep
    packages = tomllib.loads((tmp_path / 'uv.lock').read_text())['package']
    assert next(p['version'] for p in packages if p['name'] == 'decompnet') == pep
    assert f'__version__ = "{version}"' in (tmp_path / 'src/decompnet/__init__.py').read_text()
    assert f'VERSION="{version}"' in (tmp_path / 'site/public/install.sh').read_text()
