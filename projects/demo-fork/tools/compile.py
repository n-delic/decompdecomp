"""Build a demo unit through the pinned compiler sandbox, including WSL on Windows."""

import sys
from pathlib import Path

from decompnet.compiler import compile_source
from decompnet.project import compiler_config

ROOT = Path(__file__).resolve().parents[1]


def compile_unit(source: Path, output: Path, flags: list[str] | None = None) -> None:
    """`flags`: the compiler's own (toolchains.toml) when None."""
    compiler = {"toolchain": "gcc-2.7.2-kmc"} | ({"flags": flags} if flags is not None else {})
    cfg = compiler_config(ROOT, {"compiler": compiler})
    cfg.include_dirs = (str(ROOT / "include"),)
    result = compile_source(cfg, "", source.read_text(encoding="utf-8"), trusted=True)
    if not result.ok:
        sys.exit(result.log)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result.obj)


if __name__ == "__main__":
    compile_unit(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:] or None)
