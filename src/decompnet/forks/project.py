"""A decomp project on disk: what kind it is, what it needs, how to set it up, build a unit and diff it.

Layouts set up automatically:
- manifest:  the project ships `.decomp.wtf.json`: {"game": {"what", "path", "sha1"}, "setup": [[cmd...], ...]}.
- dtk:       GameCube/Wii (and similar) projects from dtk-template: configure.py + config/<version>/config.yml,
             built with ninja; the disc image goes in orig/<version>/.
- configure: other configure.py + ninja projects (many PS2 and DS decomps).
- splat:     N64/PlayStation-style projects (splat yaml + Makefile); the game file goes where the yaml's
             target_path says and is checked against its sha1.
Everything else (CMake, justfiles, custom scripts) is set up by the user following its README; once the build
has produced objdiff.json, decompdecomp takes over. Nearly every project on decomp.dev ends up with one, since
that's how its progress is reported.
"""

from __future__ import annotations

import collections
import functools
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from decompnet.hostenv import NO_WINDOW, console_python

from .. import consoles, toolchains
from ..limits import CompilerMemoryWatchdog, capped, killed_for_memory
from ..network import NetworkError
from . import objdiff

STATE = ".decomp-wtf"
# What a project covers (its manifest's `scope`): the game's own code, with the code its set-aside list names (library
# code, middleware, an SDK, the C runtime) kept from agents and out of progress; or the whole program, every function
# open and counted. A manifest without one covers the game's own code, as every project did before there was a choice.
# The scope decides only that: an import analyses the same code whatever it is. Code an older import left out
# altogether (no functions, no source files) a console can bring in later without a new import (Console.unanalysed);
# such a manifest may still say what as `set_aside_unimported`, which nothing reads any more.
GAME, PROGRAM = "game", "program"
SCOPES = (GAME, PROGRAM)
_CONFIG_CACHE: dict[str, tuple] = {}  # objdiff.json path -> (stat key, parsed, units by name)
_CONFIG_LOCK = threading.Lock()
_DOCKER_SETUP_LOCK = threading.Lock()
# Agents may only edit source and header files, never the project's build setup, targets or verification.
EDITABLE = re.compile(r"\.(c|cc|cpp|cxx|h|hh|hpp|hxx|inc|inl)$", re.I)
FORBIDDEN_PARTS = {".git", STATE, "orig", "build", "asm", "expected", "tools", "config", ".github", "assets", "bin"}


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ForkError(NetworkError):
    """Shown to the user or agent as is."""


@dataclass
class Unit:
    name: str
    target: Path | None
    base: Path | None
    source: str | None  # path relative to the project root
    complete: bool
    scratch: dict | None
    options: dict | None = None  # the unit's own objdiff options
    mappings: dict | None = None  # objdiff symbol mappings (target name -> base name)
    auto: bool = False  # split off by dtk because no file covers it (auto_00_80003100_text)


@functools.lru_cache(maxsize=64)
def _git_env(root: Path) -> dict | None:
    """Commits need an identity: when git has none set up on this computer, commits are by decompdecomp."""
    res = subprocess.run(["git", "config", "user.email"], cwd=root, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if res.returncode == 0 and res.stdout.strip():
        return None
    who = {"GIT_AUTHOR_NAME": "decompdecomp", "GIT_AUTHOR_EMAIL": "noreply@decompdecomp.invalid"}
    return {**os.environ, **who, "GIT_COMMITTER_NAME": who["GIT_AUTHOR_NAME"], "GIT_COMMITTER_EMAIL": who["GIT_AUTHOR_EMAIL"]}


# dtk-template's configure.py: Object(NonMatching, "path.c"), also over several lines, MatchingFor("GALE01")...
_OBJECT = re.compile(r'\bObject\(\s*(NonMatching|Matching|Equivalent|MatchingFor\([^()]*\))\s*,\s*"([^"]+)"', re.S)
_SRC_DIR = re.compile(r'^\s*config\.src_dir\s*=\s*Path\(\s*"([^"]+)"\s*\)', re.M)
_CHECK = re.compile(r"^build (\S+[/\\]ok): check\b")  # dtk-template writes build\<version>\ok on Windows
# build.ninja's top-level variables (`PY = /path/python`) and rules' commands (`  command = /path/tool ...`): the
# program each runs first (build_problem)
_NINJA_PROGRAM = re.compile(r"^(?:([A-Za-z_][\w.-]*)|\s+command)\s*=\s*(.*)$")
# A target the build couldn't make: ninja's "FAILED: out.o", make's "make: *** [Makefile:12: out.o] Error 1"
_FAILED = re.compile(r"^FAILED: (.+)$|^make(?:\[\d+\])?: \*\*\* \[(?:[^\]]*?:\d+: )?([^\]]+)\] Error")
_BUILD_ERROR = re.compile(r"(?:fatal error:|\berror:|\bError:|No such file or directory|"
                          r"command not found|cannot execute binary|ModuleNotFoundError:|Traceback \(most recent|^within )")
# CodeWarrior's linker warnings are harmless and can run to thousands of lines.
_NOISE = re.compile(r"^#{1,3}\s*(mwldeppc\.exe Linker Warning:|FORCEACTIVE symbol|(doesn't )?exist\.\s+Ignored\.|"
                    r"Section '\.note\.split' is unknown)")


def _tool_dirs() -> list[Path]:
    """Where decompdecomp's own build tools (the ninja Python package's ninja) are: beside its Python, or, in the
    Windows setup's bundle (packages installed with --target), in the `bin` folder beside those packages, which the
    bundle's launcher didn't put on PATH."""
    bundled = Path(__file__).resolve().parents[2] / "bin"
    return [Path(sys.executable).parent, *([bundled] if bundled.is_dir() else [])]


def _tool(name: str) -> str | None:
    """A build tool from PATH or from decompdecomp's own environment (the ninja Python package)."""
    found = shutil.which(name)
    if found:
        return found
    exe = f"{name}.exe" if os.name == "nt" else name
    return next((str(d / exe) for d in _tool_dirs() if (d / exe).exists()), None)


# #97: a public dtk project hashes the files it was built from, and some of those (a main.dol made from an ELF,
# a .rel module) aren't on the retail disc. dtk stops at the hash; say what it means and what the user can do.
_HASH_MISMATCH = re.compile(r"Hash mismatch: expected ([0-9a-fA-F]+)(?:\.{3}|…)?, but was ([0-9a-fA-F]+)", re.I)
_DTK_OBJECT = re.compile(r"While (?:loading|processing) object '([^']+)'")


def _hash_mismatch(tail: str) -> str | None:
    """What dtk's file hash mismatch means for the user, or None when this failure isn't one."""
    m = _HASH_MISMATCH.search(tail)
    if m is None:
        return None
    objects = _DTK_OBJECT.findall(tail[:m.start()])  # the file just before the hash: the one dtk was loading
    if not objects:
        return None
    obj, (expected, got) = objects[-1], m.groups()
    return (f"your copy's {obj} isn't the one this project was built against (the project expects hash "
            f"{expected[:8]}..., yours is {got[:8]}...). Your dump is likely fine: some projects are set up from "
            "files that aren't on the retail disc (check the project's README for which file and where it comes "
            "from). Or start the game from your own copy instead: choose New project → Open a game file.")


class ForkProject:
    def __init__(self, root: Path, *, create_state: bool = True):
        self.root = Path(root)
        if create_state:
            (self.root / STATE).mkdir(parents=True, exist_ok=True)

    def _game_consoles(self):
        """The named console, or each console's file preparation when a public project's platform is unknown."""
        from .. import consoles

        return [consoles.get(self.platform)] if self.platform else consoles.startable()

    # ------------------------------------------------------------------ what is it

    @property
    def manifest(self) -> dict | None:
        p = self.root / ".decomp.wtf.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def update_manifest(self, **keys) -> dict:
        """Change keys of `.decomp.wtf.json` (a None removes one), keeping the rest; returns the new manifest."""
        manifest = self.manifest or {}
        for k, v in keys.items():
            if v is None:
                manifest.pop(k, None)
            else:
                manifest[k] = v
        (self.root / ".decomp.wtf.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    @property
    def scope(self) -> str:
        """What the project covers (SCOPES): PROGRAM only when its manifest says so."""
        return PROGRAM if (self.manifest or {}).get("scope") == PROGRAM else GAME

    def kind(self) -> str:
        """What kind of build the project has: decompdecomp's own manifest, dtk-template, a configure.py, splat
        with a Makefile, an objdiff.json alone, or something else."""
        if self.manifest:
            return "manifest"
        if (self.root / "configure.py").exists() and list(self.root.glob("config/*/config.yml")):
            return "dtk"
        if (self.root / "configure.py").exists():
            return "configure"
        if self._splat_yaml() and (self.root / "Makefile").exists():
            return "splat"
        if (self.root / "objdiff.json").exists():
            return "objdiff"
        return "custom"

    def readme(self, limit: int = 70) -> str:
        """The README's build/setup section (plain text, from the repository: untrusted, display only)."""
        for name in ("README.md", "README.MD", "readme.md", "README.rst", "README.txt", "README"):
            p = self.root / name
            if p.exists():
                lines = p.read_text(errors="replace", encoding="utf-8").splitlines()
                break
        else:
            return ""
        def heading(i: int) -> bool:  # markdown "# Title" or an underlined (setext/rst) title, not a badge row
            if "![" in lines[i] or "[!" in lines[i]:
                return False
            if lines[i].lstrip().startswith("#"):
                return True
            return bool(lines[i].strip()) and i + 1 < len(lines) and bool(re.fullmatch(r"\s*[=-]{3,}\s*", lines[i + 1]))

        wanted = re.compile(r"build|setup|set up|getting started|usage|install|requirements|prerequisite", re.I)
        start = next((i for i in range(len(lines)) if heading(i) and wanted.search(lines[i])), 0)
        return "\n".join(lines[start:start + limit]).strip()

    def versions(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("config/*/config.yml"))

    def detect_platform(self) -> str:
        """Read the console declared by a public project's manifest or Splat config without running its code."""
        if value := (self.manifest or {}).get("platform"):
            return value
        # Public objdiff projects declare their console on per-unit scratch
        # configurations. Accept a single consistent connector identity only.
        try:
            objdiff = json.loads((self.root / 'objdiff.json').read_text(encoding='utf-8'))
            units = objdiff.get('units', [])
            declared = {consoles.get(unit['scratch']['platform']).id for unit in units
                        if isinstance(unit, dict) and isinstance(unit.get('scratch'), dict)
                        and isinstance(unit['scratch'].get('platform'), str)}
            known = {c.id for c in consoles.CONSOLES}
            if len(declared) == 1 and declared <= known:return next(iter(declared))
            inferred = {console.id for console in consoles.CONSOLES if console.recognizes_objdiff(objdiff)}
            if len(inferred) == 1:return next(iter(inferred))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        path = self._splat_yaml()
        if path is None:
            return ""
        import yaml

        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):
            return ""
        options = config.get("options") if isinstance(config, dict) else None
        value = options.get("platform") if isinstance(options, dict) else None
        if not isinstance(value, str):
            return ""
        console = consoles.get(value.strip().lower())
        return console.id if console.id in {c.id for c in consoles.CONSOLES} else ""

    def _splat_yaml(self) -> Path | None:
        for p in sorted(self.root.glob("*.yaml")) + sorted(self.root.glob("config/*.yaml")) + sorted(self.root.glob("config/**/*.yaml")):
            try:
                text = p.read_text(errors="replace", encoding="utf-8")
            except OSError:
                continue
            if "segments:" in text and "options:" in text:
                return p
        return None

    def _splat_version_configs(self) -> dict[str, Path]:
        """Only infer editions from the YAML template the project's Makefile actually uses."""
        makefile = self.root / "Makefile"
        if not makefile.is_file():
            return {}
        make = makefile.read_text(errors="replace", encoding="utf-8")
        basename = re.search(r"^BASENAME\s*[:?]?=\s*([A-Za-z0-9_-]+)\s*$", make, re.M)
        if not basename:
            return {}
        suffix = "$(BASENAME).$(VERSION).yaml"
        nested = "config/$(VERSION)/" + suffix
        if nested in make:
            paths = self.root.glob("config/*/" + basename[1] + ".*.yaml")
        elif suffix in make:
            paths = self.root.glob(basename[1] + ".*.yaml")
        else:
            return {}
        configs = {}
        for path in sorted(paths):
            version = path.name[len(basename[1]) + 1:-5]
            if (re.fullmatch(r"[A-Za-z0-9._-]+", version) and
                    (nested not in make or path.parent.name == version) and
                    self.root.resolve() in path.resolve().parents):
                configs[version] = path
        return configs

    def splat_baseroms(self) -> dict[str, str]:
        """Original ROM hashes for direct versioned splits and generated decompressed images.

        Direct configs supply the original hash. For generated images the Makefile names the input/output
        and the README supplies original hashes; a decompressed-image hash is never a cartridge hash.
        """
        if (getattr(self, "platform", None) or self.detect_platform()) != "n64":
            return {}
        makefile = self.root / "Makefile"
        if not makefile.is_file():
            return {}
        make = makefile.read_text(errors="replace", encoding="utf-8")
        # Direct versioned splits must select the same edition for placement and Make.
        # Require the build's explicit template, not just similarly named YAML files.
        configs = self._splat_version_configs()
        if configs:
            import yaml

            originals = {}
            for version, path in configs.items():
                try:
                    config = yaml.safe_load(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, yaml.YAMLError):
                    return {}
                if not isinstance(config, dict) or not isinstance(config.get("options"), dict):
                    return {}
                options = config["options"]
                digest = config.get("sha1", options.get("sha1"))
                base = path.parent / str(options.get("base_path", "."))
                if not (options.get("target_path") == f"baserom.{version}.z64"
                        and base.resolve() == self.root.resolve()
                        and isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{40}", digest)):
                    return {}
                originals[version] = digest.lower()
            return originals
        if not (re.search(r"^BASEROM\s*[:?+]?=\s*baserom\.\$\(VERSION\)\.z64\s*$", make, re.M) and
                re.search(r"^DECOMPRESSED_BASEROM\s*[:?+]?=\s*decompressed\.\$\(VERSION\)\.z64\s*$", make, re.M)):
            return {}
        readme = self.root / "README.md"
        if not readme.is_file():
            return {}
        text = readme.read_text(errors="replace", encoding="utf-8")
        configured = {p.name.removeprefix("decompressed.").removesuffix(".yaml")
                      for p in self.root.glob("decompressed.*.yaml")}
        return {version: digest.lower() for version, digest in re.findall(
            r"`?baserom\.([A-Za-z0-9._-]+)\.z64`?\s*:\s*`?([0-9a-fA-F]{40})\b", text)
                if version in configured}

    def splat_default_version(self) -> str | None:
        versions = self.splat_baseroms()
        if not versions:
            return None
        make = (self.root / "Makefile").read_text(errors="replace", encoding="utf-8")
        match = re.search(r"^VERSION\s*[:?]?=\s*([A-Za-z0-9._-]+)", make, re.M)
        return match.group(1) if match and match.group(1) in versions else next(iter(versions))

    def _docker_only_macos(self) -> bool:
        """Some public decomps explicitly support macOS only through their Dockerfile."""
        if sys.platform != "darwin" or not (self.root / "Dockerfile").is_file():
            return False
        readme = self.root / "README.md"
        return readme.is_file() and bool(re.search(r"macOS.*Docker only|Docker only.*macOS", readme.read_text(
            errors="replace", encoding="utf-8"), re.I | re.S))

    def _docker_image(self) -> str:
        paths = (self.root / "Dockerfile", self.root / "packages.txt")
        digest = hashlib.sha1(str(self.root.resolve()).encode())
        for path in paths:
            if path.is_file():
                digest.update(path.read_bytes())
        return f"decompdecomp-project-{digest.hexdigest()[:16]}"

    def _docker_command(self, command: list[str]) -> list[str]:
        arch = ["--platform", "linux/amd64"] if platform.machine() == "arm64" else []
        return ["docker", "run", "--rm", *arch, "--volume", f"{self.root.resolve()}:/project",
                "--workdir", "/project", "--entrypoint", "/bin/sh", self._docker_image(), "-c",
                shlex.join(command)]

    def _prepare_docker(self, log) -> None:
        with _DOCKER_SETUP_LOCK:
            if getattr(self, "_docker_ready", False):
                return
            if not shutil.which("docker"):
                raise ForkError("this project builds on macOS through Docker, but Docker is not installed. "
                                "Install Docker or a Docker-compatible runtime, then retry setup.")
            ok, tail = self.run(["docker", "info", "--format", "{{.ServerVersion}}"], log=log, timeout=15)
            if not ok:
                raise ForkError("this project needs a running Docker engine on macOS. Start it, then retry setup.\n" + tail)
            image = self._docker_image()
            ok, _ = self.run(["docker", "image", "inspect", image], log=lambda *_: None, timeout=15)
            if not ok:
                arch = ["--platform", "linux/amd64"] if platform.machine() == "arm64" else []
                log("building the project's Docker image with its MIPS toolchain")
                ok, tail = self.run(["docker", "build", *arch, "--tag", image, "."], log=log, timeout=3600)
                if not ok:
                    raise ForkError("the project's Docker image could not be built:\n" + tail)
            if (self.root / ".gitmodules").is_file():
                ok, tail = self.run(["git", "submodule", "update", "--init", "--recursive"], log=log, timeout=900)
                if not ok:
                    raise ForkError("the project's submodules could not be fetched:\n" + tail)
            if ".venv/bin/python3" in (self.root / "Makefile").read_text(errors="replace", encoding="utf-8") and (
                    self.root / "requirements.txt").is_file() and not (self.root / ".venv/bin/python3").exists():
                log("installing the project's Python requirements inside Docker")
                ok, tail = self.run(self._docker_command(["python3", "-m", "venv", ".venv"]), log=log, timeout=600)
                if ok:
                    ok, tail = self.run(self._docker_command([".venv/bin/pip", "install", "-r", "requirements.txt"]),
                                        log=log, timeout=1200)
                if not ok:
                    raise ForkError("the project's Python requirements could not be installed:\n" + tail)
            self._docker_ready = True

    def _inside(self, rel: str) -> str | None:
        """`rel` as a clean path inside the project, or None if it points outside or is a template."""
        if not rel or "__" in rel or "$" in rel or "{" in rel:
            return None
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            return None
        out = Path(os.path.relpath(p, self.root.resolve())).as_posix()
        return out + "/" if rel.endswith("/") else out

    def progress_save(self) -> tuple[Path, dict] | None:
        """A portable save at the root or beside a standalone rebuild, never through links."""
        for folder in (self.root, self.root / "progress"):
            manifest = folder / "decompdecomp.json"
            if not manifest.exists():
                continue
            if folder.is_symlink() or manifest.is_symlink():
                raise ForkError("Saved progress must be a regular folder and manifest, not symbolic links.")
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                # (app/saves.FORMATS: format 2 keeps its notes and verification records in files beside this one)
                if data.get("format") not in (1, 2) or not re.fullmatch(r"[0-9a-fA-F]{40}", data["game"]["sha1"]):
                    raise ValueError("unsupported progress manifest")
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                raise ForkError(f"Fix the saved progress manifest at {manifest}: {e}") from None
            return folder, data
        return None

    def needs(self, version: str | None = None) -> dict:
        """What the user has to provide: {"what", "path" (None: set up by hand), "sha1", "help", "auto"}."""
        saved = self.progress_save()
        if saved:
            _, manifest = saved
            return {"what": "your copy of the game for this saved progress", "path": "baserom.z64",
                    "sha1": manifest["game"]["sha1"], "auto": True, "saved_progress": True,
                    "help": "decompdecomp will restore the bundled sources, drafts, names, notes and compiler settings, and verify saved matches against your game."}
        kind = self.kind()
        manual = {"what": "your copy of the game", "path": None, "sha1": None, "auto": False,
                  "help": "This project has its own setup. Follow its README in a terminal, in the project folder; "
                          "once its build has produced objdiff.json, click Check again."}
        if kind == "manifest":
            g = self.manifest.get("game", {})
            path = self._inside(g.get("path", ""))
            return {"what": g.get("what", "your copy of the game"), "path": path, "sha1": g.get("sha1"),
                    "help": g.get("help", ""), "auto": path is not None}
        if kind == "dtk":
            v = version or (self.versions()[0] if self.versions() else "")
            return {"what": "your copy of the game's disc (ISO, GCM, RVZ, WIA, WBFS, CISO, NFS or GCZ)",
                    "path": f"orig/{v}/", "sha1": None, "auto": True,
                    "help": f"Version {v}. The build checks the disc's main executable against the project's hash."}
        if kind == "configure":
            v = version or ""
            if v and (self.root / "orig" / v).is_dir():
                return {"what": "your copy of the game (see the README for the exact file)", "path": f"orig/{v}/",
                        "sha1": None, "auto": True, "help": "The build checks the game against the project's hashes."}
            return manual
        if kind == "splat":
            originals = self.splat_baseroms()
            if originals:
                selected = version if version in originals else self.splat_default_version()
                docker_help = (" On macOS, setup uses the project's Dockerfile and requires a running Docker engine."
                               if self._docker_only_macos() else "")
                return {"what": f"your original game ROM (baserom.{selected}.z64)",
                        "path": f"baserom.{selected}.z64", "sha1": originals[selected], "auto": True,
                        "rom_versions": [{"version": v, "sha1": digest} for v, digest in originals.items()],
                        "help": "Supply an original cartridge ROM in .z64, .v64 or .n64 byte order. Its normalized "
                                "SHA-1 selects the build version; decompdecomp saves a .z64 copy for the project. "
                                "The selected version is passed to extraction and subsequent builds."
                                + docker_help}
            import yaml

            config_path = self._splat_yaml()
            try:
                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, yaml.YAMLError):
                return manual
            options = config.get("options") if isinstance(config, dict) else None
            if not isinstance(options, dict):
                return manual
            target, base = options.get("target_path"), options.get("base_path", ".")
            digest = config.get("sha1", options.get("sha1"))
            sha1 = digest.lower() if isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{40}", digest) else None
            # Use the same effective YAML values as Splat, including the final duplicate key.
            # Nested configs resolve their base against their own directory, not the checkout root.
            path = self._inside(str(config_path.parent / base / target)) if (
                isinstance(target, str) and isinstance(base, str)) else None
            if path is None:
                return manual
            return {"what": f"your copy of the game ({Path(path).name})", "path": path,
                    "sha1": sha1, "auto": True,
                    "help": ("Checked against the SHA-1 in the project's splat config. " if sha1 else
                             "This splat config does not specify a SHA-1, so decompdecomp cannot check the game "
                             "file by hash before setup. Check the project's README for the required version. ") +
                            "For PlayStation projects, a disc image can supply the expected file."}
        return manual

    # ------------------------------------------------------------------ setup

    def place_game(self, src: Path, filename: str, version: str | None = None, move: bool = False, log=None) -> Path:
        """Put the user's game file where the project expects it, checking its sha1 when the project has one.
        Works from a file on disk, so multi-gigabyte discs are never read into memory."""
        need = self.needs(version)
        if not need["path"]:
            raise ForkError("decompdecomp doesn't know where this project expects the game; follow its README")
        src = Path(src)
        if not need["path"].endswith(("/", ".z64")):
            from ..rom import RomError

            dest = self.root / need["path"]
            if src.resolve() == dest.resolve():
                if need.get("sha1") and _sha1(src) != need["sha1"]:
                    raise ForkError("The game already in this project does not match its expected SHA-1.")
                return dest
            try:
                for console in self._game_consoles():
                    if console.prepare_fork_game(src, dest, need.get("sha1"), move=move):
                        return dest
            except RomError as error:
                raise ForkError(str(error)) from error
        supplied_sha1 = _sha1(src) if need.get("sha1") or need["path"].endswith(".z64") else None
        details = f"Supplied file SHA-1: {supplied_sha1}"
        normalized_sha1 = None
        if need["path"].endswith(".z64"):
            from ..consoles.n64.rom import copy_z64_file, file_hashes

            hashes = file_hashes(src, supplied_sha1)
            details, normalized_sha1 = hashes.describe(), hashes.normalized_sha1
            if log:
                log(details)
        if need.get("sha1") and supplied_sha1 != need["sha1"]:
            if need["path"].endswith(".z64"):
                if normalized_sha1 == need["sha1"]:
                    dest = self.root / need["path"]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    copy_z64_file(src, dest)
                    return dest
            inner = self._from_disc(src.read_bytes(), Path(need["path"]).name) if src.stat().st_size < 1 << 30 else None
            if inner is None or hashlib.sha1(inner).hexdigest() != need["sha1"]:
                raise ForkError("That file does not match the version this project expects.\n"
                                f"Expected SHA-1: {need['sha1']}\n{details}\n"
                                "Check the project's README for the required region and revision.")
            dest = self.root / need["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(inner)
            return dest
        dest = self.root / need["path"]
        if need["path"].endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / (Path(filename).name or "game.iso")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
        # A user may select the game already placed by setup, including through a symlink or hard link.
        # Validate its hash above, then leave it in place instead of copying or moving it onto itself.
        if dest.exists() and src.samefile(dest):
            return dest
        if move:
            shutil.move(str(src), dest)
        else:
            try:
                os.link(src, dest)  # no second copy of a big disc when it's on the same drive
            except OSError:
                shutil.copyfile(src, dest)
        return dest

    @staticmethod
    def _from_disc(data: bytes, name: str) -> bytes | None:
        """A file (e.g. SLUS_013.95) taken from a PlayStation disc image (ISO 9660)."""
        from ..consoles.ps2 import disc

        if not disc.is_disc(data):
            return None
        try:
            return disc._Disc(data).find(name)
        except (ValueError, IndexError, KeyError):
            return None

    def setup_commands(self, version: str | None = None) -> list[list[str]]:
        """The commands that set the project up for a version of its game, as its kind of build does it."""
        kind = self.kind()
        if kind == "manifest":
            m = self.manifest
            cmds = m.get("setup", [])
            if m.get("created") == "from scratch" and cmds:  # point at this computer's tools, not the creator's
                from .. import consoles

                args = consoles.get(m.get("platform")).tool_args()
                first = cmds[0]
                keep = next((i for i, c in enumerate(first) if c.startswith("--")), len(first))
                cmds = [[*first[:keep], *args], *cmds[1:]]
            return [[sys.executable if c in ("python", "python3") else c for c in cmd] for cmd in cmds]
        if kind == "dtk":
            cmd = [sys.executable, "configure.py"]
            if version and len(self.versions()) > 1:
                cmd += ["--version", version]
            return [cmd, [_tool("ninja") or "ninja"]]
        if kind == "configure":
            return [[sys.executable, "configure.py"], [_tool("ninja") or "ninja"]]
        if kind == "splat":
            make = (self.root / "Makefile").read_text(errors="replace", encoding="utf-8")
            chosen = [f"VERSION={version}"] if version and self.splat_baseroms() else []
            steps = [["make", *chosen, t] for t in ("setup", "extract") if re.search(rf"^{t}\s*:", make, re.M)]
            jobs = min(os.cpu_count() or 4, 4) if self._docker_only_macos() else os.cpu_count() or 4
            return steps + [["make", *chosen, f"-j{jobs}"]]
        if kind == "objdiff":
            cfg = self.objdiff_config()
            return [[cfg.get("custom_make") or "make", *cfg.get("custom_args", [])]]
        raise ForkError("decompdecomp can't tell how to build this project; follow its README, then refresh")

    def run(self, cmd: list[str], log=print, timeout: float = 3600, failed: list[str] | None = None,
            platform: str | None = None) -> tuple[bool, str]:
        """Run a build step in the project. Targets the build reports as failed (ninja's FAILED:, make's Error lines)
        are added to `failed`."""
        console = consoles.get((self.manifest or {}).get("platform") or platform or self.detect_platform())
        if self._docker_only_macos() and Path(cmd[0]).name in ("make", "gmake"):
            self._prepare_docker(log)
            cmd = self._docker_command(["make", *cmd[1:]])
        else:
            cmd = console.project_build_command(self, cmd)
        env = dict(os.environ)
        # Existing project environments carry its pinned extractor dependencies. Do not install into the app.
        python = next((folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python3')
                       for folder in (self.root / '.venv', self.root / 'venv')
                       if (folder / 'pyvenv.cfg').is_file() and
                       (folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python3')).is_file()), None)
        project_python = [python.parent] if python else []
        if python:
            env['VIRTUAL_ENV'] = str(python.parent.parent)
        project_tools = console.project_tool_paths(self)
        env.update(console.build_env())
        env["PATH"] = os.pathsep.join([*(str(d) for d in [*project_python, *_tool_dirs(), *project_tools]), env.get("PATH", "")])
        # found on that PATH (decompdecomp's own ninja): Linux looks a command up there itself, Windows only in this
        # process's PATH, where decompdecomp's environment isn't when it's installed
        executable = str(python or sys.executable) if cmd[0] in ("python", "python3") else shutil.which(cmd[0], path=env["PATH"]) or cmd[0]
        cmd = [console_python(executable), *cmd[1:]]  # not pythonw.exe: its tools' compilers would flash consoles
        log("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
        try:
            proc = subprocess.Popen(capped(cmd), cwd=self.root, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace", encoding="utf-8", creationflags=NO_WINDOW,
                                    start_new_session=os.name != "nt")
        except FileNotFoundError:
            return False, f"{cmd[0]} isn't installed"
        from .. import compiler_processes
        compiler_processes.track(proc.pid)
        primary_failure = False
        code = None
        watchdog = None
        try:
            with CompilerMemoryWatchdog(proc, timeout=timeout) as watchdog:
                out, seen, quiet = [], collections.Counter(), 0
                for line in proc.stdout:
                    line = line.rstrip()
                    out.append(line)
                    if failed is not None and (m := _FAILED.match(line)):
                        failed.extend(t for t in (m.group(1) or m.group(2)).split() if t not in failed and not t.startswith("[code="))
                    seen[line] += 1
                    if seen[line] > 2 or _NOISE.match(line):  # the same line again, or CodeWarrior's linker chatter
                        quiet += 1
                    elif len(out) <= 400 or len(out) % 50 == 0 or _BUILD_ERROR.search(line):
                        log(line)
                try:
                    code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    primary_failure = True
                    proc.kill()
                    return False, "timed out"
        except BaseException:
            primary_failure = True
            raise
        finally:
            compiler_processes.finish(proc, failed=primary_failure or code not in (None, 0) or bool(
                watchdog and (watchdog.timed_out or watchdog.exceeded)))
        if watchdog.timed_out:
            return False, f"Build timed out after {timeout}s; its compiler processes were stopped."
        if watchdog.exceeded:
            out.append(watchdog.message())
            # The process may exit normally while the watchdog's signal races
            # with it (or is denied). Crossing the limit still fails the build.
            if code == 0:code = 1
        if quiet:
            log(f"({quiet} repeated lines and linker warnings not shown)")
        if killed_for_memory(code):
            out.append("stopped: it used more memory than decompdecomp allows a single tool, so it was stopped "
                       "before it could slow down or freeze your computer")
        # what failed, not the warnings after it: dsd's hundreds of "[WARN ] No module for relocation" lines came after
        # a compiler's error and pushed it out of the tail agents and the user read
        said = [ln for ln in out if not _NOISE.match(ln) and not ln.lstrip().startswith("[WARN")] or out
        problems = [line for line in said if _BUILD_ERROR.search(line)] if code else []
        tail = "\n".join(dict.fromkeys([*problems[:12], *said[-60:]]))
        if code:
            from .buildhelp import explain

            if hints := explain(self.root, executable, "\n".join(out)):
                log(hints)
                tail = hints + "\n\nBuild output:\n" + tail
        return code == 0, tail

    def unpack_disc(self, version: str | None, log=print) -> int:
        """A dtk project that reads an extracted disc (orig/<version>/sys/main.dol, files/...) instead of the disc
        image: copy the files its config names out of the user's disc image with decompdecomp's own dtk, as the README's
        "extract the disc with Dolphin" step would. Returns how many files were copied."""
        if self.kind() != "dtk" or not version:
            return 0
        cfg, orig = self.root / "config" / version / "config.yml", self.root / "orig" / version
        if not cfg.is_file() or not orig.is_dir():
            return 0
        text = cfg.read_text(errors="replace", encoding="utf-8")
        if re.search(r"^\s*object_base\s*:", text, re.M):
            return 0  # this dtk reads the disc image itself
        wanted = sorted({m.group(1) for m in re.finditer(rf"orig/{re.escape(version)}/([^\s'\"#,\]]+)", text)})
        missing = [w for w in wanted if not w.endswith("/") and ".." not in w and not (orig / w).exists()]
        if not missing:
            return 0
        from ..consoles.gc_wii import scratch as dtk  # dtk projects are GameCube and Wii ones

        discs = [(p, dtk.disc_id(p)) for p in sorted(orig.iterdir()) if p.is_file() and p.stat().st_size > 1 << 20]
        discs = [(p, gid) for p, gid in discs if gid]
        disc = next((p for p, gid in discs if gid == version), None) or (discs[0][0] if len(discs) == 1 else None)
        if disc is None:
            return 0  # no disc image here (or only other versions): the project's own error says what's missing
        log(f"taking the {len(missing)} files the project reads out of your disc image ({disc.name})")
        by_dir: dict[str, list[str]] = {}
        for w in missing:
            by_dir.setdefault(str(Path(w).parent), []).append(w)
        for parent, files in by_dir.items():
            dtk.vfs_copy(disc, files, orig / parent)
        return len(missing)

    def setup(self, version: str | None = None, log=print) -> None:
        """Set the project up for the user's copy of the game: unpack it, run the setup commands, and build
        every source once so objdiff can measure each unit."""
        self.unpack_disc(version, log=log)
        for cmd in self.setup_commands(version):
            ok, tail = self.run(cmd, log=log)
            make_step = Path(cmd[0]).name.removesuffix('.exe') in ('make', 'gmake')
            if (not ok and make_step and not (self.root / 'objdiff.json').exists()
                    and re.search(r'computed checksum did NOT match|build succeeded, but did not match', tail)):
                # A linked but incomplete decomp can still be measured. The generator must independently
                # verify every original reference section; compiler and missing-tool failures keep failing.
                log('The whole-image check failed; trying independently verified original references.')
                try:
                    self.generate_objdiff(version=version, log=log)
                except ForkError as error:
                    log(f'Original references could not be verified: {error}')
            # Incomplete sources may fail make once objdiff can measure individual units. Without that
            # configuration, a failed build is a setup failure, not a successful build missing its report.
            if not ok and (not make_step or not (self.root / "objdiff.json").exists()):
                if mismatch := _hash_mismatch(tail):
                    raise ForkError(f"{mismatch}\n{tail}")
                raise ForkError(f"setup step failed: {' '.join(cmd)}\n{tail}")
        if (self.root / "objdiff.json").exists() and self._all_source():
            log("compiling every source file, so objdiff can measure each one (some work in progress may not compile)")
            self.build_all(log=log)
        if not (self.root / "objdiff.json").exists():
            self.generate_objdiff(version=version, log=log)
        if not (self.root / "objdiff.json").exists():
            raise ForkError("the project built, but has no objdiff.json, so decompdecomp can't measure matches. "
                            "Some projects generate it with an extra step; check the README.")

    def generate_objdiff(self, version: str | None = None, log=print) -> bool:
        """Try the console's supported reference generator without changing an existing configuration."""
        if (self.root / "objdiff.json").exists():
            return False
        path = self._splat_yaml()
        if version:
            path = self._splat_version_configs().get(version) or next(
                (p for p in self.root.glob("*.yaml") if p.name.endswith(f".{version}.yaml")), path)
        if path is None:
            return False
        import yaml
        from elftools.common.exceptions import ELFError

        from .. import consoles

        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(config, dict) or not isinstance(config.get("options"), dict):
                raise ForkError("automatic objdiff setup needs a supported splat configuration")
            console = consoles.get(config["options"].get("platform"))
            segments = config.get('segments')
            if (isinstance(segments, list) and not any(
                    isinstance(segment, dict) and segment.get('type') == 'code' for segment in segments)
                    and any(self.root.glob('*/Makefile'))):
                from .splatnested import generate

                return generate(self, path, config, console, version=version, log=log)
            return console.generate_objdiff(self, path, config, log=log)
        except (OSError, ValueError, ELFError, yaml.YAMLError, subprocess.TimeoutExpired, toolchains.ToolchainError) as error:
            raise ForkError(f"automatic objdiff setup could not finish: {error}. Check the project's setup manually.") from None

    def build_problem(self) -> str | None:
        """Why the project's build can't run on this computer as it is, or None: no objdiff.json, or a program the
        generated build.ninja runs by absolute path (its interpreter, a tool) that isn't there. A project folder
        copied from another home kept /home/olduser/.local/share/uv/tools/decompnet/bin/python in its build.ninja,
        and every compile failed with "/bin/sh: 1: ...: not found" until its setup ran again."""
        if not (self.root / "objdiff.json").exists():
            return "the project has no objdiff.json (its setup didn't finish)"
        ninja = self.root / "build.ninja"
        if not ninja.is_file():
            return None
        missing: list[str] = []
        try:
            with open(ninja, errors="replace", encoding="utf-8") as f:
                for line in f:
                    m = _NINJA_PROGRAM.match(line)
                    if m is None:
                        continue
                    first = m.group(2).strip().strip("'\"").split()[0] if m.group(2).strip() else ""
                    prog = first.strip("'\"")
                    # (a folder path, /home/...: not an MSVC flag like /O2 or /DNDEBUG)
                    if re.match(r"^/[^/\s]+/", prog) and prog not in missing and not Path(prog).exists():
                        missing.append(prog)
        except OSError as e:
            return f"build.ninja can't be read: {e}"
        if missing:
            return (f"build.ninja runs {', '.join(missing[:3])}, which {'is' if len(missing) == 1 else 'are'} not on "
                    "this computer (the project was set up elsewhere, or decompdecomp moved)")
        return None

    def reconfigure(self, version: str | None = None, log=print) -> tuple[bool, str]:
        """Write the build files again for this computer: the setup's first step (configure.py, with this
        computer's tool paths), not the whole setup's builds."""
        try:
            cmds = self.setup_commands(version)
        except ForkError as e:
            return False, str(e)
        if self.kind() not in ("manifest", "dtk", "configure") or not cmds:
            return False, "this kind of project's setup can't be run again by itself"
        return self.run(cmds[0], log=log, timeout=600)

    # ------------------------------------------------------------------ objdiff project

    def _config_entry(self) -> tuple[tuple, dict, dict]:
        """(stat key, objdiff.json, units by name), parsed once per change of the file: a big game's runs to
        megabytes and every compile needs it."""
        p = self.root / "objdiff.json"
        try:
            st = p.stat()
        except OSError:
            raise ForkError("objdiff.json not found: set the project up first") from None
        key = (st.st_mtime_ns, st.st_size)
        with _CONFIG_LOCK:
            hit = _CONFIG_CACHE.get(str(p))
            if hit is None or hit[0] != key:
                for wait in range(40):  # configure.py rewrites it when a file is linked: read it whole
                    try:
                        cfg = json.loads(p.read_text(encoding="utf-8"))
                        break
                    except (ValueError, OSError):
                        if wait == 39:
                            if hit is not None:
                                return hit  # the last whole one: units rarely change between two writes
                            raise ForkError("objdiff.json can't be read (it's being rewritten): try again") from None
                        time.sleep(0.15)
                st = p.stat()
                key = (st.st_mtime_ns, st.st_size)
                by_name: dict[str, Unit] = {}
                for u in self._units_of(cfg):
                    by_name.setdefault(u.name, u)
                hit = _CONFIG_CACHE[str(p)] = (key, cfg, by_name)
            return hit

    def objdiff_config(self) -> dict:
        return self._config_entry()[1]

    def _units_of(self, cfg: dict) -> list[Unit]:
        tdir, bdir = cfg.get("target_dir"), cfg.get("base_dir")
        out = []
        for u in cfg.get("units") or cfg.get("objects") or []:
            meta = u.get("metadata") or {}
            t = u.get("target_path") or (str(Path(tdir) / u["path"]) if tdir and u.get("path") else None)
            b = u.get("base_path") or (str(Path(bdir) / u["path"]) if bdir and u.get("path") else None)
            out.append(Unit(u.get("name") or u.get("path") or "?", self.root / t if t else None,
                            self.root / b if b else None, meta.get("source_path"),
                            bool(meta.get("complete", u.get("complete", False))), u.get("scratch"),
                            u.get("options") or None, u.get("symbol_mappings") or None, bool(meta.get("auto_generated"))))
        return out

    def units(self) -> list[Unit]:
        return self._units_of(self.objdiff_config())

    def unit(self, name: str) -> Unit:
        found = self._config_entry()[2].get(name)
        if found is None:
            raise ForkError(f"no unit {name!r}")
        return found

    def units_for(self, rel: str) -> list[Unit]:
        """The units whose source file is `rel` (relative to the project root)."""
        want = Path(rel).as_posix()
        return [u for u in self.units() if u.source and Path(u.source).as_posix() == want]

    def diff_options(self, unit: Unit) -> dict:
        """What every diff of this unit is judged by: the progress report's rules with the project's and unit's options."""
        return objdiff.judge_options(self.objdiff_config().get("options"), unit.options)

    def build_unit(self, unit: Unit, log=lambda *_: None) -> tuple[bool, str]:
        """Build one unit's object with the project's own build, as objdiff.json says."""
        if unit.base is None:
            return False, "this unit has no source object to build"
        cfg = self.objdiff_config()
        make = cfg.get("custom_make") or "make"
        tool = _tool(make) or make
        rel = os.path.relpath(unit.base, self.root)
        return self.run([tool, *cfg.get("custom_args", []), rel], log=log, timeout=600)

    def failed_units(self, targets: list[str]) -> set[str]:
        """Units whose source object is among the build's failed targets."""
        want = {os.path.normpath(t) for t in targets}
        return {u.name for u in self.units() if u.base is not None and os.path.normpath(os.path.relpath(u.base, self.root)) in want}

    def _all_source(self) -> bool:
        """dtk-template's `all_source`: every unit's source object, including units the game's link doesn't use yet
        (the default build only compiles what the link needs, so objdiff would have nothing to measure them by)."""
        try:
            with open(self.root / "build.ninja", errors="replace", encoding="utf-8") as f:
                return any(line.startswith("build all_source:") for line in f)
        except OSError:
            return False

    def build_all(self, log=lambda *_: None, failed: list[str] | None = None) -> tuple[bool, str]:
        """Build every object, going on past sources that don't compile yet (work in progress); `failed`
        collects the ones that didn't."""
        cfg = self.objdiff_config()
        make = cfg.get("custom_make") or "make"
        if (make == "ninja" or Path(make).name.startswith("ninja")) and self._all_source():
            # -k 0: keep going past work in progress that doesn't compile yet
            return self.run([_tool(make) or make, *cfg.get("custom_args", []), "-k", "0", "all_source"], log=log,
                            failed=failed)
        keep_going = ["-k"] if Path(make).name in ("make", "gmake", "mingw32-make") else []
        return self.run([_tool(make) or make, *cfg.get("custom_args", []), *keep_going], log=log, failed=failed)

    def report(self) -> dict:
        return objdiff.report(self.root, self.root / STATE / "report.json")

    def diff(self, unit: Unit, symbol: str) -> objdiff.FunctionDiff:
        from .linkedimages import stale
        entry = next((u for u in self.objdiff_config().get("units", []) if u.get("name") == unit.name), {})
        base = None if stale(self.root, entry, {}) else unit.base
        return objdiff.diff(unit.target, base, symbol, cwd=self.root, options=self.diff_options(unit),
                            project=self.root if unit.mappings else None, unit=unit.name)

    # ------------------------------------------------------------------ linking (dtk-template projects)
    # A file whose code and data match is linked into the game: configure.py marks it Matching, the build then
    # uses its compiled object instead of the original's, and the project's checksum check (`ninja .../ok`) proves
    # the whole game still comes out byte for byte. That's what decomp.dev counts as linked.

    def check_target(self) -> str | None:
        """dtk-template's checksum target (build/<version>/ok: the built DOL and RELs against the game's SHA-1s)."""
        try:
            with open(self.root / "build.ninja", errors="replace", encoding="utf-8") as f:
                for line in f:
                    m = _CHECK.match(line)
                    if m:
                        return m.group(1)
        except OSError:
            pass
        return None

    def link_states(self) -> dict[str, tuple[str, int, int]]:
        """Source file (relative to the project root) -> (its link state in configure.py: Matching, NonMatching,
        Equivalent or MatchingFor(...), where that word starts and ends in configure.py)."""
        try:
            return self._states_in((self.root / "configure.py").read_text(errors="replace", encoding="utf-8"))
        except OSError:
            return {}

    def src_dir(self) -> str:
        """Where configure.py's source paths start (dtk-template: config.src_dir, "src")."""
        try:
            m = _SRC_DIR.search((self.root / "configure.py").read_text(errors="replace", encoding="utf-8"))
        except OSError:
            m = None
        return (m.group(1) if m else "src").strip("/")

    @staticmethod
    def _states_in(text: str) -> dict[str, tuple[str, int, int]]:
        src_dir = _SRC_DIR.search(text)
        prefix = (src_dir.group(1) if src_dir else "src").strip("/")
        return {f"{prefix}/{m.group(2)}": (m.group(1), m.start(1), m.end(1)) for m in _OBJECT.finditer(text)}

    def linkable(self) -> bool:
        return self.check_target() is not None and bool(self.link_states())

    def marked_matching(self, text: str, sources: list[str], version: str | None) -> str:
        """configure.py (`text`) with these source files (NonMatching in it) marked as linked."""
        states = self._states_in(text)
        # a project that builds several versions links per version where it can say so
        word = (f'MatchingFor("{version}")' if version and len(self.versions()) > 1 and "def MatchingFor" in text
                else "Matching")
        spans = sorted((states[s][1], states[s][2]) for s in sources if s in states and states[s][0] == "NonMatching")
        for start, end in reversed(spans):
            text = text[:start] + word + text[end:]
        return text

    def image_verification(self) -> tuple[Path, str, list[list[str]]] | None:
        """An explicit whole-image check also protects partially decompiled source units."""
        manifest = self.manifest or {}
        check = manifest.get("verify_image")
        if check is None:
            return None
        game = manifest.get("game") or {}
        if not isinstance(check, dict):
            raise ForkError("verify_image must name an output and build commands")
        output, commands, sha1 = check.get("path"), check.get("commands"), game.get("sha1", "")
        if (not isinstance(output, str) or not output or not isinstance(sha1, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha1)
                or not isinstance(commands, list) or not commands
                or any(not isinstance(cmd, list) or not cmd
                       or any(not isinstance(arg, str) or not arg for arg in cmd) for cmd in commands)):
            raise ForkError("verify_image needs an output path, nonempty command lists and the original game's SHA-1")
        path = (self.root / output).resolve()
        if self.root.resolve() not in path.parents or path == (self.root / game.get("path", "")).resolve():
            raise ForkError("verify_image must check a rebuilt image inside the project, separate from the original")
        return path, sha1.lower(), commands

    def source_is_linked(self, unit: Unit) -> bool:
        """A source replacement must preserve an explicitly verified image, even before its unit is complete."""
        return self.image_verification() is not None or (unit.complete and self.linkable())

    def verify(self, log=lambda *_: None) -> tuple[bool, str]:
        """Build the game and check it against its checksums (regenerating the build when configure.py changed)."""
        if check := self.image_verification():
            path, expected, commands = check
            logs = []
            for command in commands:
                cmd = [sys.executable if arg in ("python", "python3") else arg for arg in command]
                ok, text = self.run(cmd, log=log, timeout=1800)
                logs.append(text)
                if not ok:
                    return False, "\n".join(logs)
            try:
                with path.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha1").hexdigest()
            except OSError as error:
                return False, f"couldn't read rebuilt image {path.relative_to(self.root)}: {error}"
            if actual != expected:
                return False, f"rebuilt image SHA-1 {actual} does not match original SHA-1 {expected}"
            return True, "\n".join(logs + [f"rebuilt image SHA-1 matches {expected}"])
        target = self.check_target()
        if target is None:
            return False, "this project has no checksum check to run"
        cfg = self.objdiff_config()
        make = cfg.get("custom_make") or "ninja"
        return self.run([_tool(make) or make, *cfg.get("custom_args", []), target], log=log, timeout=1800)

    # ------------------------------------------------------------------ files agents may touch

    def safe_path(self, rel: str, write: bool = False) -> Path:
        """A path inside the project an agent may read, or write (`write`: only C and C++ sources and headers,
        never the build setup, targets, assembly or tools)."""
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            raise ForkError("that path is outside the project")
        parts = set(Path(os.path.relpath(p, self.root.resolve())).parts)
        if write:
            if parts & FORBIDDEN_PARTS or not EDITABLE.search(p.name):
                raise ForkError("agents may only edit C/C++ source and header files, not the project's build setup, "
                                "targets, assembly or tools")
        elif ".git" in parts:
            raise ForkError("that path isn't readable")
        return p

    def git(self, *args: str) -> tuple[bool, str]:
        if not (self.root / ".git").exists() or not shutil.which("git"):
            return False, "not a git checkout"
        res = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, env=_git_env(self.root),
                             encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        return res.returncode == 0, (res.stdout + res.stderr).strip()
