"""Original SN64 compiler + assembler, with a pinned PsyQ-to-ELF converter.

Invoked as a standalone script inside the normal compiler sandbox. No game data,
tool installation or executable lookup belongs here; every tool path is explicit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def assembler_input(text: str) -> str:
    # SN's assembler predates GNU's ELF metadata directives. Function extent is
    # recovered by the ELF reader from adjacent symbols after conversion.
    text = re.sub(r"^\s*\.(?:version|type|size)\b[^\n]*", "", text, flags=re.M)
    # PsyQ's object format encodes section names, not ELF flags. Its pinned converter recognizes
    # .rdata as read-only data; GNU GCC emits the equivalent .rodata for jump tables/constants.
    text = re.sub(r'(?m)^(\s*\.section\s+)["\']?\.rodata["\']?(?=\s|,|$)', r'\1.rdata', text)
    text = re.sub(r'(?m)^\s*\.rodata\s*$', '.rdata', text)
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def compile_source(cc: Path, cpp: Path, assembler: Path, converter: Path, runner: Path,
                   source: Path, output: Path, include: Path, flags: list[str]) -> None:
    for tool in (cc, cpp, assembler, converter, runner):
        if not tool.is_file():
            raise ValueError(f'Missing {tool.name}: reinstall the SN64 toolchain and its converter in Compilers & tools.')
    def run(argv):
        subprocess.run([str(a) for a in argv], check=True, timeout=30)

    preprocessed = output.with_suffix('.sn.i')
    assembly = output.with_suffix('.sn.s')
    obj = output.with_suffix('.sn.obj')
    defines = [f for f in flags if f.startswith(('-D', '-U', '-I'))]
    compiler_flags = [f for f in flags if f not in defines]
    run([runner, cpp, '-P', '-undef', '-nostdinc', '-D_LANGUAGE_C', '-D__GNUC__=2',
         '-I' + str(include.resolve()), *defines, source, '-o', preprocessed])
    run([runner, cc, '-quiet', *compiler_flags, preprocessed, '-o', assembly])
    assembly.write_bytes(assembler_input(assembly.read_text(encoding='utf-8', errors='replace')).encode('utf-8'))
    run([runner, assembler, '-mips3', '-I', include, '-o', obj, assembly])
    run([converter, obj, '-o', output, '-b', '-n'])
    if not output.is_file() or output.read_bytes()[:6] != b'\x7fELF\x01\x02':
        raise ValueError('The SN64 converter did not produce a big-endian ELF32 object.')


def compile_native_source(cc: Path, cpp: Path, assembler: Path, wrapper: Path,
                          source: Path, output: Path, include: Path, flags: list[str]) -> None:
    """Reconstructed SN codegen with the separately pinned ASN compatibility pass."""
    for tool in (cc, cpp, assembler, wrapper):
        if not tool.is_file():
            raise ValueError(f'Missing {tool.name}: reinstall the reconstructed SN64 toolchain and dependencies.')
    defines = [f for f in flags if f.startswith(('-D', '-U', '-I'))]
    compiler_flags = [f for f in flags if f not in defines]
    preprocessed, assembly = output.with_suffix('.sn.i'), output.with_suffix('.sn.s')
    commands = [
        [cpp, '-P', '-undef', '-nostdinc', '-D_LANGUAGE_C', '-D__GNUC__=2', '-I' + str(include),
         *defines, source, '-o', preprocessed],
        [cc, '-quiet', *compiler_flags, preprocessed, '-o', assembly],
    ]
    for argv in commands:
        subprocess.run([str(a) for a in argv], check=True, timeout=30)
    small = next((f for f in reversed(flags) if f.startswith('-G')), '-G0')
    subprocess.run([sys.executable, str(wrapper), str(assembler), '-EB', '-mips3', small,
                    '-I' + str(include), '-o', str(output), str(assembly)], check=True, timeout=30)
    if not output.is_file() or output.read_bytes()[:6] != b'\x7fELF\x01\x02':
        raise ValueError('The SN64 compatibility assembler did not produce a big-endian ELF32 object.')


def compile_gcc_source(gcc: Path, assembler: Path, converter: Path, runner: Path,
                       source: Path, output: Path, include: Path, flags: list[str]) -> None:
    """Keep GCC code generation and SN assembly separate; neither instruction stream is rewritten."""
    for tool in (gcc, assembler, converter, runner):
        if not tool.is_file():
            raise ValueError(f'Missing {tool.name}: reinstall the GCC/SN pairing and its dependencies.')
    # ASN under wibo must receive absolute host/guest paths in exported relative builds.
    source, output, include = source.resolve(), output.resolve(), include.resolve()
    assembly, obj = output.with_suffix('.sn.s'), output.with_suffix('.sn.obj')
    commands = [
        [gcc, '-B' + str(gcc.parent) + '/', '-S', *flags, '-I' + str(include.resolve()), source, '-o', assembly],
        [runner, assembler, '-mips3', '-I', include, '-o', obj, assembly],
        [converter, obj, '-o', output, '-b', '-n'],
    ]
    for index, argv in enumerate(commands):
        subprocess.run([str(a) for a in argv], check=True, timeout=30)
        if index == 0:
            assembly.write_bytes(assembler_input(assembly.read_text(encoding='utf-8')).encode('utf-8'))
    if not output.is_file() or output.read_bytes()[:6] != b'\x7fELF\x01\x02':
        raise ValueError('The GCC/SN pairing did not produce a big-endian ELF32 object.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gcc', type=Path)
    for option in ('cc', 'cpp', 'assembler'):
        parser.add_argument('--' + option, type=Path)
    for option in ('converter', 'runner', 'wrapper'):
        parser.add_argument('--' + option, type=Path)
    parser.add_argument('--native', action='store_true')
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('include', type=Path)
    parser.add_argument('flags', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.gcc:
            if not all((args.assembler, args.converter, args.runner)):
                raise ValueError('The GCC/SN pairing needs its assembler, converter and runner.')
            compile_gcc_source(args.gcc, args.assembler, args.converter, args.runner,
                               args.source, args.output, args.include, args.flags)
        elif not all((args.cc, args.cpp, args.assembler)):
            raise ValueError('The SN64 profile needs its compiler, preprocessor and assembler.')
        elif args.native:
            if args.wrapper is None:
                raise ValueError('The reconstructed SN64 profile needs its pinned assembler wrapper.')
            compile_native_source(args.cc, args.cpp, args.assembler, args.wrapper,
                                  args.source, args.output, args.include, args.flags)
        else:
            if args.converter is None or args.runner is None:
                raise ValueError('The original SN64 profile needs its converter and runner.')
            compile_source(args.cc, args.cpp, args.assembler, args.converter, args.runner,
                           args.source, args.output, args.include, args.flags)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'SN64 compilation failed: {error}\n')


if __name__ == '__main__':
    main()
