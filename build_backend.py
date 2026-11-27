"""Build the checkout gateway before an editable uv install."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from uv_build import build_editable as _build_editable
from uv_build import build_sdist, build_wheel  # noqa: F401 (PEP 517 hooks)

ROOT = Path(__file__).resolve().parent


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    if (ROOT / ".git").exists() and os.environ.get("DECOMP_BUILD_GATEWAY", "").lower() not in ("0", "off", "false", "no"):
        cargo = shutil.which("cargo")
        if cargo is None:
            candidate = Path.home() / ".cargo" / "bin" / ("cargo.exe" if os.name == "nt" else "cargo")
            cargo = str(candidate) if candidate.is_file() else None
        if cargo is None:
            print("MCP gateway: Cargo is unavailable. Install Rust and run uv sync --reinstall-package decompnet.",
                  file=sys.stderr)
        else:
            try:
                subprocess.run([cargo, "build", "--locked", "--profile", "checkout",
                                "--manifest-path", str(ROOT / "gateway" / "Cargo.toml"),
                                "--target-dir", str(ROOT / "gateway" / "target")], cwd=ROOT, check=True, timeout=900)
            except (OSError, subprocess.SubprocessError) as error:
                print(f"MCP gateway: checkout build failed. The app will check for a release build ({error}).",
                      file=sys.stderr)
    return _build_editable(wheel_directory, config_settings, metadata_directory)
