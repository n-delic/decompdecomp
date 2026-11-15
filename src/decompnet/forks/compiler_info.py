"""Declared compiler settings from public build files, read without executing Make or project code.

These are declarations, not a replacement for the build system: unresolved variables and conditional values
stay visible. In particular, even `make -n` can run shell commands while loading a Makefile.
"""

from __future__ import annotations

import re

import yaml

from .. import consoles

_ASSIGN = re.compile(r"^\s*([A-Za-z_]\w*)\s*(:=|\?=|\+=|=)\s*(.*?)\s*$")
_TARGET = re.compile(r"^([^:=]+):\s*([A-Za-z_]\w*)\s*(:=|\?=|\+=|=)\s*(.*?)\s*$")
_REF = re.compile(r"\$\(([A-Za-z_]\w*)\)|\$\{([A-Za-z_]\w*)\}")
_FLAGS = ("CFLAGS", "CPPFLAGS", "OPT_FLAGS", "MIPSBIT")


def describe(project, version: str | None = None) -> dict:
    """Report what the files declare, retaining provenance and ambiguity instead of guessing effective flags."""
    variables, overrides, sources = {}, [], []
    text = ""
    files = {p.name: p for p in project.root.iterdir()}
    for name in ("GNUmakefile", "makefile", "Makefile"):
        path = files.get(name)
        if path is not None and path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            sources.append(name)
            break
    depth = 0
    declarations, conditional_commands = [], []
    for line in re.sub(r"\\\r?\n", " ", text).splitlines():
        if line.startswith("\t") and not (depth and _ASSIGN.match(line)):  # allow indented conditional declarations
            continue
        line = line.split("#", 1)[0].strip()
        assignment = _ASSIGN.match(line)
        if assignment:
            declarations.append(assignment[3])
            if depth and assignment[1] == "CC":
                conditional_commands.append(assignment[3])
        if re.match(r"if(?:eq|neq|def|ndef)\b", line):
            depth += 1
        elif line == "endif":
            depth = max(0, depth - 1)
        elif not depth:
            if target := _TARGET.match(line):
                pattern, variable, op, value = target.groups()
                if variable in _FLAGS:
                    overrides.append({"pattern": pattern.strip(), "variable": variable, "operator": op, "value": value})
            elif assignment := _ASSIGN.match(line):
                variable, op, value = assignment.groups()
                if op == "+=":
                    variables[variable] = (variables.get(variable, "") + " " + value).strip()
                elif op != "?=" or variable not in variables:
                    variables[variable] = value
    if version:
        variables["VERSION"] = version

    def expand(value, seen=()):
        def replace(match):
            variable = match[1] or match[2]
            if variable not in variables or variable in seen or len(seen) >= 16:
                return match[0]
            return expand(variables[variable], (*seen, variable))
        return _REF.sub(replace, value)

    command = expand(variables.get("CC", ""))
    name = ""
    declared_commands = [command, *(expand(c) for c in conditional_commands)]
    if any("ido" in c.lower() for c in declared_commands):
        versions = set(re.findall(r"ido(?:[-_]?(?:static[-_]?)?recomp)?(?:/build)?[-_/]?(\d+\.\d+)",
                                  "\n".join(declarations), re.I))
        name = "IDO" + (" " + next(iter(versions)) if len(versions) == 1 else "")
    elif re.search(r"(?:^|[/\s-])(?:gcc|g\+\+)(?:$|[-\s])", command):
        name = "GCC"
    elif "clang" in command:
        name = "Clang"
    elif re.search(r"\bmwcc", command):
        name = "CodeWarrior"
    splat = project._splat_yaml()
    if splat:
        try:
            # The segment map can contain thousands of entries. LibYAML's safe loader
            # avoids a long Python-only parse on the foreground project-switch request.
            config = yaml.load(splat.read_text(encoding="utf-8"), Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))
            options = config.get("options") if isinstance(config, dict) else None
            compiler = options.get("compiler") if isinstance(options, dict) else None
            if isinstance(compiler, str):
                name = name or compiler
                sources.append(splat.relative_to(project.root).as_posix())
        except (OSError, ValueError, yaml.YAMLError):
            pass
    manifest = project.manifest or {}
    compiler = manifest.get("compiler")
    if isinstance(compiler, dict):
        name = consoles.compiler_name(compiler["toolchain"]) if compiler.get("toolchain") else name
    elif isinstance(compiler, str):
        name = compiler
    flags = [{"variable": key, "value": expand(variables[key])} for key in _FLAGS if variables.get(key)]
    if isinstance(compiler, dict) and isinstance(compiler.get("flags"), list):
        flags = [{"variable": "flags", "value": " ".join(compiler["flags"])}]
    if compiler:
        sources.insert(0, ".decomp.wtf.json")
    for item in overrides:
        item["pattern"] = expand(item["pattern"])
        item["value"] = expand(item["value"])
    return {"name": name or command, "command": command, "flags": flags, "overrides": overrides, "sources": sources,
            "note": "Declared build settings; unresolved variables and conditional settings depend on the build."}
