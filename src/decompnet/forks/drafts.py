"""Machine-made first drafts for functions of a fork that are still assembly.

Community projects keep the assembly of undecompiled functions in the tree: splat projects one file per
function (`asm/nonmatchings/<file>/<function>.s`, referenced by `INCLUDE_ASM`), dtk projects one file per
unit under `build/<version>/asm/`. m2c turns that into a C draft for MIPS (N64, PS2, and public PS1 decomps),
PowerPC (GameCube, Wii), ARM and Thumb (public GBA and DS decomps).
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import consoles, seeds, toolchains

MAX_ASM = 2 * 1024 * 1024


def supported(platform: str) -> bool:
    """Whether m2c reads the code of this platform's console (its m2c target: consoles/)."""
    return bool(consoles.get(platform).m2c_target())


def find_asm(root: Path, unit_name: str, symbol: str, source_text: str | None) -> Path | None:
    """The assembly file holding `symbol`, if the project has one."""
    if source_text:
        m = re.search(rf'INCLUDE_ASM\(\s*"([^"]+)"\s*,\s*{re.escape(symbol)}\s*\)', source_text)
        if m and (root / m.group(1) / f"{symbol}.s").is_file():
            return root / m.group(1) / f"{symbol}.s"
        m = re.search(rf'GLOBAL_ASM\(\s*"([^"]*/{re.escape(symbol)}\.s)"\s*\)', source_text)
        if m and (root / m.group(1)).is_file():
            return root / m.group(1)
    for base in (root / "asm" / "nonmatchings", root / "asm"):
        if base.is_dir():
            for hit in base.rglob(f"{symbol}.s"):
                return hit
    # dtk: build/<version>/asm/<unit path>.s, unit names being "<module>/<path>"
    parts = unit_name.split("/")
    for asm_root in root.glob("build/*/asm"):
        for rel in ("/".join(parts), "/".join(parts[1:])):
            if rel and (asm_root / f"{rel}.s").is_file():
                return asm_root / f"{rel}.s"
    return None


def _block(text: str, symbol: str) -> str:
    """Just one function from a large dtk assembly file."""
    m = re.search(rf"^\.fn {re.escape(symbol)}\b.*?^\.endfn {re.escape(symbol)}\b.*?$", text, re.M | re.S)
    return m.group(0) if m else text


def draft(root: Path, platform: str, unit_name: str, symbol: str, source_path: str | None,
          compiler_hint: str = "", timeout: float = 30, valid: bool = False) -> str | None:
    """m2c's draft of one function; `valid`: in a form that compiles (m2c's macros for what it couldn't tell, which
    from-scratch projects define in common.h)."""
    target = consoles.get(platform).m2c_target(compiler_hint)
    if not target or toolchains.installed("m2c") is None:
        return None
    src = root / source_path if source_path else None
    source_text = src.read_text(errors="replace", encoding="utf-8") if src and src.exists() else None
    asm = find_asm(root, unit_name, symbol, source_text)
    lang = "c++" if source_path and Path(source_path).suffix.lower() in (".cpp", ".cc", ".cxx", ".cp") else "c"
    if asm is None:
        return None
    text = asm.read_text(errors="replace", encoding="utf-8")
    if len(text) > MAX_ASM or ".fn " in text:
        text = _block(text, symbol)
    return seeds.run_m2c(text, symbol, f"{target}-{lang}", timeout=timeout, valid=valid)


def add_m2c_macros(header: Path) -> bool:
    """The macros compilable drafts use (M2C_FIELD, M2C_UNK...), added to a from-scratch project's common header.
    False when they're there already."""
    try:
        text = header.read_text(encoding="utf-8")
    except OSError:
        return False
    if "M2C_DEFINES" in text:
        return False
    block = "\n/* What machine drafts (m2c) use for what they can't tell yet: autopilot tries them as they are. */\n" \
            + seeds.M2C_MACROS
    end = text.rstrip().rfind("#endif")
    text = text[:end] + block + "\n" + text[end:] if end >= 0 else text.rstrip("\n") + "\n" + block
    header.write_text(text, encoding="utf-8")
    return True


_DECL = re.compile(r"^[A-Za-z_][^;{}=]*;\s*(?:/\*.*\*/)?$")  # a declaration: no body, no initializer


def self_contained(text: str, span: tuple[int, int], file_text: str = "") -> str:
    """One function out of an m2c draft, as a definition that compiles on its own in its file: what m2c declares
    outside it (the globals and functions it uses) goes inside, at the top of its body, where C and C++ allow
    declarations too. Data m2c would define (`static`) is only declared. Names the file declares already (with
    their real types, often) are left to it."""
    code = text[span[0]:span[1]]
    decls = []
    for line in (text[:span[0]] + "\n" + text[span[1]:]).splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//", "/*")) or not _DECL.match(line):
            continue
        line = re.sub(r"/\*.*\*/\s*$", "", line).strip()
        line = re.sub(r"^static\s+", "extern ", line)
        name = re.search(r"(\w+)\s*(?:\(|\[|;)", line)
        if name and file_text and re.search(rf"\b{re.escape(name.group(1))}\b", file_text):
            continue
        if "(" in line or line.startswith("extern "):
            decls.append(line)
        else:
            decls.append("extern " + line)
    brace = code.find("{")
    if brace < 0 or not decls:
        return code
    return code[:brace + 1] + "\n" + "".join(f"    {d}\n" for d in dict.fromkeys(decls)) + code[brace + 1:].lstrip("\n")
