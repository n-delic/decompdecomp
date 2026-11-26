"""Compile decomprepair's optional native modules inside the package copy a bundle just installed.

    build_library_native.py SITE_PACKAGES

Run it with Cython and setuptools available and the bundle's Python version (the build scripts do:
`uv run --no-project --python 3.12 --with cython --with setuptools`), after the library is installed. It runs the
library's own `native_build.py` on that folder (its default: the typed kernels, seconds), so the compiled `.so`/`.pyd`
files sit beside the `.py` files and the bundle imports them. `DECOMP_NATIVE_LIBRARY=all` also compiles the plain
modules (a minute or two): a signed bundle never compiles at run time (app/native_library.py). Like build_fastelf.py it never fails the build: an older library without native_build.py, no
C compiler, or `DECOMP_NATIVE_LIBRARY=0` leave the interpreted modules, which behave identically (a few percent slower).
"""
import os
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if os.environ.get("DECOMP_NATIVE_LIBRARY") == "0":
        print("DECOMP_NATIVE_LIBRARY=0: the library stays interpreted.")
        return 0
    package = Path(argv[0]) / "decomprepair" if argv else None
    script = package / "native_build.py" if package else None
    if script is None or not script.is_file():
        print("This decomprepair has no native_build.py: the library stays interpreted.")
        return 0
    try:
        everything = ["--all"] if os.environ.get("DECOMP_NATIVE_LIBRARY") == "all" else []
        subprocess.run([sys.executable, str(script), "--package", str(package), *everything], check=True)
    except (OSError, subprocess.CalledProcessError) as error:  # the optional speed-up is skipped, never the build
        print(f"The library's native modules could not be built ({error}): it stays interpreted.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
