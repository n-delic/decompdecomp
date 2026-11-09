"""Fork packages carry provenance without needing a checkout or install.json."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from decompnet.app import build_info


def test_package_build_stamp_matches_committed_source(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'packaging/stamp_build.py'
    spec = importlib.util.spec_from_file_location('stamp_build', path)
    stamp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stamp)
    root, package = tmp_path / 'repo', tmp_path / 'decompnet'
    root.mkdir(); package.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'code').write_text('committed')
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-qm', 'source'], check=True)
    stamp.stamp(root, package)
    metadata = json.loads((package / 'build-info.json').read_text())
    assert len(metadata['commit']) == 40 and metadata['fork'] == 'RetroReversing'
    (root / 'code').write_text('dirty')
    with pytest.raises(ValueError, match='Commit source changes'):
        stamp.stamp(root, package)


def test_fork_label_includes_version_and_commit(monkeypatch):
    monkeypatch.setattr(build_info, 'commit', lambda: 'a' * 40)
    assert 'RetroReversing fork' in build_info.label()
    assert 'aaaaaaaaaaaa' in build_info.label()
