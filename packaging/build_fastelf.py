"""Build the optional Cython symbol-table decoder into the next package wheel.

Like build_alignment.py: without Cython or a C compiler nothing is built and the app keeps pyelftools' own parser.
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / 'src' / 'decompnet' / 'bin'


def main() -> None:
    for old in BIN.glob('decomp_fastelf.*'):
        old.unlink()
    try:
        from Cython.Build import cythonize
        from setuptools import Extension
        from setuptools.dist import Distribution
    except ImportError:
        print('Cython or setuptools is unavailable. This package will use pyelftools\' own symbol parser.')
        return
    with tempfile.TemporaryDirectory(prefix='decomp-fastelf-') as directory:
        source = Path(directory) / 'decomp_fastelf.pyx'
        shutil.copy2(ROOT / 'native' / 'decomp_fastelf.pyx', source)
        try:
            extensions = cythonize([Extension('decomp_fastelf', [str(source)])], build_dir=directory, quiet=True)
            dist = Distribution({'ext_modules': extensions})
            command = dist.get_command_obj('build_ext')
            command.build_lib = directory
            command.build_temp = str(Path(directory) / 'tmp')
            command.ensure_finalized()
            command.run()
        except Exception as error:  # no C compiler, SDK or headers: the optional speed-up is skipped
            print(f'The symbol decoder could not be built ({error}). This package will use pyelftools\' parser.')
            return
        built = next(Path(directory).glob('decomp_fastelf*.*'), None)
        if built is None or built.suffix not in ('.so', '.pyd', '.dylib'):
            built = next((p for p in Path(directory).rglob('decomp_fastelf*') if p.suffix in ('.so', '.pyd')), None)
        if built is None:
            print('The symbol decoder was not produced. This package will use pyelftools\' parser.')
            return
        BIN.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, BIN / built.name)
    print(f'Built symbol decoder at {(BIN / built.name).relative_to(ROOT)}')


if __name__ == '__main__':
    sys.exit(main())
