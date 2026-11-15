"""Build the MCP gateway (gateway/, Rust) and put it in the package, where installs look for it.

    python packaging/build_gateway.py [--target TRIPLE] [--asset DIR [--platform NAME] [--no-package]]

The package builds (packaging/*/build.*) run this before `uv build --wheel`, so the wheel carries
`decompnet/bin/decomp-mcp-gateway[.exe]` and every install has the gateway (app/mcp_gateway.py, docs/MCP_GATEWAY.md).
It needs cargo. DECOMP_BUILD_GATEWAY=0 skips it: that package then falls back to the app's own MCP endpoint.
`--asset DIR` also writes the release's standalone copy, `decomp-mcp-gateway-<platform>[.exe]` and its `.sha256`,
which `decomp setup` downloads for installs made from source (app/mcp_gateway.py: install).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "decomp-mcp-gateway" + (".exe" if os.name == "nt" else "")
DESTINATION = ROOT / "src" / "decompnet" / "bin" / NAME


def portable_target() -> str | None:
    """On Linux, a static musl build when rustup has the target: one built against the build machine's glibc doesn't
    start on an older distribution (the app then falls back to its own endpoint, but the package should not)."""
    if not sys.platform.startswith("linux"):
        return None
    target = f"{platform.machine()}-unknown-linux-musl"
    rustup = shutil.which("rustup") or str(Path.home() / ".cargo" / "bin" / "rustup")
    try:
        installed = subprocess.run([rustup, "target", "list", "--installed"], capture_output=True, text=True,
                                   timeout=60).stdout.split()
    except (OSError, subprocess.SubprocessError):
        installed = []
    if target in installed:
        return target
    print(f"note: {target} isn't installed (rustup target add {target}); the gateway is built for this machine's "
          "glibc and may not start on older distributions", file=sys.stderr)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", help="a Rust target triple (default: this computer's)")
    parser.add_argument("--asset", type=Path, help="also write the release's standalone copy and its .sha256 here")
    parser.add_argument("--platform", help="the asset's platform (default: this computer's, as decompnet names it)")
    parser.add_argument("--no-package", action="store_true", help="leave src/decompnet/bin as it is")
    args = parser.parse_args()
    if os.environ.get("DECOMP_BUILD_GATEWAY", "").strip().lower() in ("0", "off", "false", "no"):
        DESTINATION.unlink(missing_ok=True)  # an old build must not ship by accident
        print("MCP gateway skipped (DECOMP_BUILD_GATEWAY=0): the package uses the app's own MCP endpoint")
        return 0
    cargo = shutil.which("cargo") or next((str(p) for p in [Path.home() / ".cargo" / "bin" / ("cargo.exe" if os.name == "nt" else "cargo")]
                                           if p.is_file()), None)
    if cargo is None:
        print("cargo isn't installed (https://rustup.rs). Install it, or set DECOMP_BUILD_GATEWAY=0 to package without "
              "the MCP gateway.", file=sys.stderr)
        return 1
    target = args.target or portable_target()
    command = [cargo, "build", "--release", "--locked", "--manifest-path", str(ROOT / "gateway" / "Cargo.toml")]
    if target:
        command += ["--target", target]
    env = dict(os.environ)
    if os.name == "nt":
        # Without the Visual C++ runtime DLLs on PATH (the portable ZIP puts only Python's own folder there).
        env["RUSTFLAGS"] = (env.get("RUSTFLAGS", "") + " -C target-feature=+crt-static").strip()
    subprocess.run(command, check=True, env=env)
    built = ROOT / "gateway" / "target" / (target or "") / "release" / NAME
    if not args.no_package:
        place(built, DESTINATION)
        print(f"MCP gateway: {DESTINATION.relative_to(ROOT)} ({DESTINATION.stat().st_size / 2**20:.1f} MiB)")
    if args.asset:
        plat = args.platform or this_platform()
        asset = args.asset / f"decomp-mcp-gateway-{plat}{'.exe' if plat.startswith('windows') else ''}"
        place(built, asset)
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        asset.with_name(asset.name + ".sha256").write_text(f"{digest}  {asset.name}\n", encoding="ascii")
        print(f"release asset: {asset} (sha256 {digest})")
    return 0


def place(built: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, destination)
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def this_platform() -> str:
    """decompnet's name for this computer (toolchains.current_platform), without importing the package."""
    os_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "arm64" if os_name == "macos" else "aarch64",
               "aarch64": "aarch64" if os_name != "macos" else "arm64"}.get(machine, machine)
    return f"{os_name}-{machine}"


if __name__ == "__main__":
    sys.exit(main())
