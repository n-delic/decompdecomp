"""Whole-image checks protect partial source units as well as completed files."""
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from decompnet.forks.project import ForkError, ForkProject


def configured(root, commands=None):
    manifest = {"game": {"path": "original.bin", "sha1": hashlib.sha1(b"original").hexdigest()},
                "verify_image": {"path": "build/image.bin", "commands": commands or [[sys.executable, "build.py"]]}}
    (root / ".decomp.wtf.json").write_text(json.dumps(manifest))
    return ForkProject(root)


def test_verified_image_protects_incomplete_units_and_checks_output(tmp_path):
    fp = configured(tmp_path)
    assert fp.source_is_linked(SimpleNamespace(complete=False))
    (tmp_path / "build.py").write_text(
        "from pathlib import Path\np=Path('build/image.bin')\np.parent.mkdir(exist_ok=True)\np.write_bytes(b'original')\n")
    assert fp.verify()[0]
    (tmp_path / "build.py").write_text("from pathlib import Path\nPath('build/image.bin').write_bytes(b'changed')\n")
    ok, message = fp.verify()
    assert not ok and "does not match original SHA-1" in message


def test_failed_verification_command_cannot_use_stale_matching_image(tmp_path):
    fp = configured(tmp_path, [[sys.executable, "-c", "raise SystemExit(1)"]])
    (tmp_path / "build").mkdir()
    (tmp_path / "build/image.bin").write_bytes(b"original")
    assert not fp.verify()[0]


@pytest.mark.parametrize("output", ["original.bin", "../outside.bin"])
def test_verification_cannot_compare_the_original_or_an_external_image(tmp_path, output):
    fp = configured(tmp_path)
    check = fp.manifest["verify_image"] | {"path": output}
    fp.update_manifest(verify_image=check)
    with pytest.raises(ForkError, match="rebuilt image inside"):
        fp.verify()


def test_missing_rebuilt_image_fails_and_unconfigured_partial_units_stay_unlinked(tmp_path):
    fp = configured(tmp_path, [[sys.executable, "-c", "pass"]])
    assert not fp.verify()[0]
    fp.update_manifest(verify_image=None)
    assert not fp.source_is_linked(SimpleNamespace(complete=False))
