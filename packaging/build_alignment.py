"""Build the optional Rust scorer library into the next package wheel."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from decompnet.instruction_alignment import library_name  # noqa: E402


def main() -> None:
    name = library_name()
    destination = ROOT / 'src' / 'decompnet' / 'bin' / name
    destination.unlink(missing_ok=True)
    rustc = shutil.which('rustc')
    if rustc is None:
        fallback = Path.home() / '.cargo' / 'bin' / ('rustc.exe' if sys.platform == 'win32' else 'rustc')
        if not fallback.is_file():
            print('rustc is unavailable. This package will use Python instruction alignment.')
            return
        rustc = str(fallback)

    with tempfile.TemporaryDirectory(prefix='decomp-alignment-') as directory:
        built = Path(directory) / name
        subprocess.run([rustc, '--crate-type', 'cdylib', '--edition', '2021', '-O', '-C', 'panic=abort',
                        str(ROOT / 'native' / 'alignment.rs'), '-o', str(built)], check=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, destination)
    print(f'Built instruction alignment library at {destination.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
