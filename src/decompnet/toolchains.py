"""Pinned compiler toolchains: registry, verified install, and lookup for the compile sandbox.

A toolchain is one or more upstream archives, each verified against a pinned sha256 and
extracted into its own directory. Projects name a toolchain in project.toml; the compiler
sandbox mounts that directory (and any runtime it requires) read-only.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import urllib.request
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from decompnet.hostenv import NO_WINDOW, find_uv

from . import limits, linuxvm, wsl

MANIFEST = ".decompnet-toolchain.json"
# in each version's folder of a pack with `headers`: the other Include folders its compiler searches, one per line,
# relative to the folder (the build reads it: consoles/pc/project_template/tools/configure.py)
INCLUDES = "includes.txt"
# what of a toolchain's entry decides what its install holds on disk (its fingerprint, installed()); a part's
# `platforms` only says which computers take it
INSTALLED_FROM = ("requires", "git", "commit", "pip_extra")
LINUX = "linux-x86_64"
# Parts for WSL: Linux programs packed in a format Windows can unpack (a .deb, not a .zst)
WSL_PLATFORM = "wsl-x86_64"


class ToolchainError(RuntimeError):
    pass


@dataclass
class Toolchain:
    id: str
    name: str
    license: str
    source: str
    description: str = ""
    kind: str = "compiler"  # compiler | runtime | tool
    platform: str = ""
    arch: str = ""
    cc: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    cmd: list[str] = field(default_factory=list)  # full command template, when compiling is more than cc + flags
    parts: list[dict] = field(default_factory=list)
    git: str = ""
    commit: str = ""
    binary_name: str = ""  # main executable of a tool, without .exe
    pip_extra: list[str] = field(default_factory=list)  # extra requirements for a git tool's environment
    requires_java: int = 0  # minimum Java version (Ghidra)
    prelude: str = ""  # C put before every file it compiles: works around a compiler's own bugs (see toolchains.toml)
    intrinsics: str = ""  # pinned module providing fixed compiler intrinsic templates
    cplusplus: bool = False  # it compiles C++ too (by the file's extension: .cpp)
    driver: str = ""  # packaged, console-owned compiler adapter, staged read-only with the tools
    build: str = ""  # native source-built tools, after checksum-verified parts are extracted
    family: str = ""  # backend family when the registry id doesn't carry its compiler's name
    wine: bool = False  # Windows programs wibo cannot run: use wine on Linux and macOS
    headers: list[str] = field(default_factory=list)  # shared compiler header packs
    local_files: list[str] = field(default_factory=list)
    local_places: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.requires = [*self.requires, *(h for h in self.headers if h not in self.requires)]
    linux_vm: bool = False  # this Linux pack may use the optional macOS VM runner
    space_free_runtime: bool = False  # an old driver embeds unquoted paths derived from its own executable
    case_sensitive: bool = False  # proxy hosts must preserve archives until extraction on Linux
    assembly_preprocess: list[str] = field(default_factory=list)
    assembly_compile: list[str] = field(default_factory=list)

    @property
    def local(self) -> bool:
        return bool(self.local_files)

    @property
    def proprietary(self) -> bool:
        return self.license.lower() == "proprietary"

    def parts_for(self, plat: str | None = None) -> list[dict]:
        plat = plat or current_platform()
        return [p for p in self.parts if plat in p.get("platforms", [plat])]

    @property
    def via_wsl(self) -> bool:
        """A Linux-only toolchain on Windows: installed from its Linux build and run inside WSL 2 (see wsl.py)."""
        return wsl.wanted() and not self.git and not self.parts_for() and bool(self.parts_for(LINUX))

    @property
    def via_vm(self) -> bool:
        return (self.linux_vm and not self.via_wsl and current_platform().startswith("macos-")
                and linuxvm.wanted() and not self.parts_for() and bool(self.parts_for(LINUX)))

    def linux_parts(self) -> list[dict]:
        return [p for p in self.parts if WSL_PLATFORM in p.get("platforms", [])] or self.parts_for(LINUX)

    @property
    def supported(self) -> bool:
        """Whether this toolchain can be installed on this computer (on Windows, Linux ones through WSL 2)."""
        return self.local or bool(self.git) or bool(self.parts_for()) or self.via_wsl or self.via_vm


def current_platform() -> str:
    """linux-x86_64, linux-aarch64, macos-x86_64, macos-arm64 or windows-x86_64."""
    os_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
    machine = _platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "arm64" if os_name == "macos" else "aarch64",
               "aarch64": "aarch64" if os_name != "macos" else "arm64"}.get(machine, machine)
    return f"{os_name}-{machine}"


def unsupported_cpu() -> str | None:
    """Supported hosts; Apple Silicon uses native builds or Intel Mac tools through Rosetta.
    Each tool still checks that its own build is available before installation."""
    host = current_platform()
    machine = host.split("-", 1)[1]
    if machine == "x86_64" or host == "macos-arm64":
        return None
    return (f"decompdecomp needs a 64-bit Intel or AMD PC for now (the games' compilers are x86 programs; this "
            f"computer's processor is {machine})")


@functools.cache
def _registry_data() -> dict:
    """toolchains.toml's entries, and the kits users bring (kits/: local toolchains, nothing to download)."""
    from . import kits

    return {**tomllib.loads(resources.files("decompnet").joinpath("toolchains.toml").read_text(encoding="utf-8")),
            **{k.id: k.toolchain() for k in kits.KITS}}


def registry() -> dict[str, Toolchain]:
    return {tid: Toolchain(id=tid, **spec) for tid, spec in _registry_data().items()}


def known(tid: str) -> bool:
    """Whether the registry has this toolchain id. A project's config can name one it doesn't: the project was made
    with another tool (#118: a Windows project named a 64-bit Visual C++ one), and it must still open."""
    return tid in _registry_data() or _own(tid) is not None


def _own(tid: str):
    """A compiler of the user's own kit copies (kits.own_compiler) that a ROM project names as its toolchain; never
    for a registry id (a kit's own id is one: looking its copies up would ask for it again)."""
    from . import kits

    return None if tid in _registry_data() else kits.own_compiler(tid)


def _registered(tid: str) -> Toolchain | None:
    """One packaged entry, excluding dynamic compiler versions from user SDK copies."""
    spec = _registry_data().get(tid)
    return Toolchain(id=tid, **spec) if spec is not None else None


def get(tid: str) -> Toolchain:
    # A compile needs one entry, not fresh objects for every compiler and SDK.
    if (tc := _registered(tid)) is not None:
        return tc
    if (own := _own(tid)) is not None:
        return Toolchain(id=tid, name=own.name, license="proprietary", source="your own copy: nothing is downloaded",
                         description="A compiler in your own SDK copy.", flags=list(own.flags))
    raise ToolchainError(f"unknown toolchain {tid!r}; known: {', '.join(sorted(_registry_data()))}")



def source_prelude(tc, source: str) -> str:
    """Carry used fixed intrinsics into portable C exports, including direct compiler builds.

    The app injects these templates itself; an exported Makefile/build.py may invoke the period
    compiler directly. Include only templates referenced by the source/context so unrelated
    checkpoints keep their existing headers and source lines.
    """
    import importlib
    intrinsic = getattr(tc, 'intrinsics', '')
    if not intrinsic:return tc.prelude
    module = importlib.import_module(intrinsic)
    header = module.header_for(source) if hasattr(module,'header_for') else module.header()
    provided = set(re.findall(r'^#define[ \t]+([A-Za-z_]\w*)', header, re.M))
    used = set(re.findall(r'[A-Za-z_]\w*', source))
    return tc.prelude + (header if provided & used else '')


def compiler_family(tid: str) -> str:
    """An explicitly declared backend family; custom command ids may be unknown."""
    return _registry_data().get(tid, {}).get("family", "")


def diagnostic_flags(tid: str) -> tuple[str, ...]:
    """Warning switches for known GCC backends only; no tools are installed to answer this."""
    spec = _registry_data().get(tid, {})
    gcc = spec.get('family') == 'gcc' or 'gcc' in tid.split('-')
    return ('-Wall', '-Wuninitialized') if gcc and spec else ()


def data_dir() -> Path:
    """Per-user data directory for decompnet (toolchains, caches)."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "decompnet"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "decompnet"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "decompnet"


def root() -> Path:
    env = os.environ.get("DECOMPNET_TOOLCHAINS")
    return Path(env).expanduser() if env else data_dir() / "toolchains"


def path(tid: str, linux: bool | None = None, *, vm: bool | None = None) -> Path:
    """Where a toolchain is installed; VM and WSL copies are separate from native installations."""
    if vm is None:
        vm = linux is None and tid in _registry_data() and get(tid).via_vm
    if vm:
        return root() / 'linux-vm' / tid
    if linux is None:
        linux = tid in _registry_data() and get(tid).via_wsl
    return root() / "wsl" / tid if linux else root() / tid


def installed(tid: str, linux: bool | None = None, *, vm: bool | None = None) -> dict | None:
    """Return the current installed manifest, including a user-supplied kit compiler."""
    manifest = _manifest(tid, linux, vm=vm)
    if manifest is None and _own(tid) is not None:
        return {"own": True}
    if manifest is None:
        return None
    if not get(tid).local and (changed(tid, manifest, path(tid, linux, vm=vm)) or _unbuilt(tid, linux)):
        return None
    tc = _registered(tid)
    if tc is not None and any(installed(h, linux, vm=vm) is None for h in tc.headers):
        return None
    return manifest


def _manifest(tid: str, linux: bool | None = None, *, vm: bool | None = None) -> dict | None:
    m = path(tid, linux, vm=vm) / MANIFEST
    return json.loads(m.read_text(encoding="utf-8")) if m.exists() else None


# Ghidra's folder of native programs for each computer (ghidra.framework.Platform)
# (Windows on Arm runs the release's Intel one, emulated: Ghidra's GettingStarted.md)
GHIDRA_OS = {"linux-x86_64": "linux_x86_64", "linux-aarch64": "linux_arm_64", "macos-x86_64": "mac_x86_64",
             "macos-arm64": "mac_arm_64", "windows-x86_64": "win_x86_64", "windows-aarch64": "win_x86_64"}


def ghidra_decompiler(ghidra: Path, plat: str | None = None) -> Path:
    """Where the Ghidra in `ghidra` looks for its decompiler on this computer."""
    plat = plat or current_platform()
    name = "decompile.exe" if plat.startswith("windows") else "decompile"
    return ghidra / "Ghidra" / "Features" / "Decompiler" / "os" / GHIDRA_OS.get(plat, plat) / name


def _unbuilt(tid: str, linux: bool | None) -> bool:
    """Whether an install lacks the native program it can't work without: Ghidra's decompiler, which its release
    brings for Intel Linux and Windows and its install builds elsewhere (build = "ghidra-decompiler"). Without it
    Ghidra's analysis gives no function a calling convention. A Ghidra installed on a Mac before decompdecomp built
    its decompiler has none, and one whose decompiler went missing on any computer neither: either is installed
    again (ensure), not used without it."""
    if tid != "ghidra" or tid not in _registry_data():
        return False
    linux = get(tid).via_wsl if linux is None else linux
    return not ghidra_decompiler(path(tid, linux), LINUX if linux else None).is_file()


def fingerprint(tc: Toolchain, linux: bool = False) -> str:
    """What an install of this entry holds, as a short hash: its parts (archives, hashes, where each goes) and what
    else INSTALLED_FROM names. Its name and description don't change an install."""
    parts = tc.linux_parts() if linux else tc.parts_for()
    entry = {"parts": [{k: v for k, v in p.items() if k != "platforms"} for p in parts],
             **{k: getattr(tc, k) for k in INSTALLED_FROM}}
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:16]


def changed(tid: str, manifest: dict, where: Path | None = None) -> bool:
    """Whether toolchains.toml's entry changed since this install: a pack moved its Windows SDK into a toolchain of its
    own, and an install that kept the SDK inside would have built without the SDK's new folder. An install
    made before installs recorded their entry's fingerprint is judged by the archives it records; one that still
    matches is given the fingerprint (in `where`, its folder, when it can be written), so a later change to the entry
    is seen too."""
    tc = _registered(tid)
    if tc is None:  # (a project names a toolchain decompdecomp doesn't have: known())
        return False
    linux = bool(manifest.get("wsl") or manifest.get("linux_vm"))
    now = fingerprint(tc, linux)
    if "fingerprint" in manifest:
        return manifest["fingerprint"] != now
    if "parts" not in manifest:  # (not one install() wrote)
        return False
    parts = tc.linux_parts() if linux else tc.parts_for()
    urls = {p["url"] for p in parts} | {v for p in parts for k, v in p.items() if k.endswith("_url")}
    had = {(p.get("url"), p.get("sha256")) for p in manifest["parts"]}
    if {url for url, _ in had} != urls or (manifest.get("commit") or "") != tc.commit or \
            any(p.get("sha256") and (p["url"], p["sha256"]) not in had for p in parts):
        return True
    try:
        if where is not None:
            _write_manifest(where, {**manifest, "fingerprint": now})
    except OSError:
        pass  # (a folder it can't write, read-only: judged by its archives again next time)
    return False


def _write_manifest(folder: Path, manifest: dict) -> None:
    """An install's manifest, written whole or not at all: installed() reads it while /api/state, builds and other
    installs run, and changed() adds the fingerprint to an older one during such a read. Written to a file beside
    it, then moved over it in one step (os.replace), so a reader sees the old manifest or the new, never half."""
    tmp = folder / f"{MANIFEST}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(tmp, folder / MANIFEST)
    finally:
        tmp.unlink(missing_ok=True)


@functools.cache
def _intel_mac_runtime_ready() -> bool:
    try:
        return subprocess.run(["/usr/bin/arch", "-x86_64", "/usr/bin/true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def runtime_error(tid: str) -> str | None:
    """Why an installed compiler cannot start: native translation, wine, or the optional Linux runner."""
    if get(tid).wine:
        from . import wine
        if why := wine.problem(get(tid).name):
            return why
    if get(tid).via_vm:
        ok, why = linuxvm.status()
        return None if ok else why
    if current_platform() != "macos-arm64" or not any(p.get("rosetta") for p in get(tid).parts_for()):
        return None
    if _intel_mac_runtime_ready():
        return None
    _intel_mac_runtime_ready.cache_clear()  # a newly installed Rosetta runtime works on the next compile
    return (f"{tid} uses Intel macOS binaries and needs Rosetta 2 on Apple Silicon. "
            "Run `softwareupdate --install-rosetta` in Terminal, accept Apple's license if you agree, then try again.")


def require(tid: str, linux: bool | None = None, *, vm: bool | None = None) -> Path:
    """Directory of an installed toolchain, or a clear error telling the operator how to install it."""
    metadata = installed(tid, linux, vm=vm)
    if metadata is None:
        section = "Compilers & tools → Tools" if get(tid).kind == "tool" else "Compilers & tools"
        raise ToolchainError(f"toolchain {tid!r} is not installed: install it in decompdecomp, under {section}")
    if get(tid).case_sensitive and (metadata.get('linux_vm') or metadata.get('wsl')) and not metadata.get('archive_parts'):
        raise ToolchainError(f'Reinstall {tid} in Compilers & tools so its case-sensitive Linux files are preserved.')
    if why := runtime_error(tid):
        raise ToolchainError(why)
    return path(tid, linux, vm=vm)


_runtime_copy_lock = threading.Lock()
_runtime_copy_home = None
_runtime_copies: dict[tuple, Path] = {}


def _space_free_runtime(directory: Path) -> Path:
    """SN's driver canonicalizes symlinks and embeds an unquoted self-relative include path.

    Copy the verified installed pack once per process, only when its real path has whitespace.
    A private temporary directory avoids changing installs or sharing writable executables between users.
    The manifest stamp changes on reinstall; existing compiles can keep using their old snapshot.
    """
    global _runtime_copy_home
    directory = directory.resolve()
    if not any(character.isspace() for character in str(directory)):
        return directory
    try:
        stamp = (directory / MANIFEST).stat()
        key = (str(directory), stamp.st_mtime_ns, stamp.st_size)
        with _runtime_copy_lock:
            if key in _runtime_copies:
                return _runtime_copies[key]
            if _runtime_copy_home is None:
                _runtime_copy_home = tempfile.TemporaryDirectory(prefix='decompnet-tools-',
                                                                 dir='/tmp' if os.name == 'posix' else None)
            cache = Path(_runtime_copy_home.name)
            if any(character.isspace() for character in str(cache.resolve())):
                raise ToolchainError('The compiler needs a temporary folder without spaces. Set TMPDIR to one and retry.')
            target = cache / hashlib.sha256(repr(key).encode()).hexdigest()[:16]
            try:
                # Materialize symlinks too: the driver asks the runner for its real executable path.
                shutil.copytree(directory, target)
            except OSError:
                shutil.rmtree(target, ignore_errors=True)
                raise
            _runtime_copies[key] = target
            return target
    except OSError as error:
        raise ToolchainError(f'Could not prepare a compiler path without spaces. Check free space and temporary-folder permissions: {error}') from None


def runtime_dirs(tid: str) -> tuple[Path, ...]:
    """The toolchain plus everything it requires, for mounting into the sandbox (all Linux builds for WSL)."""
    tc = get(tid)
    linux = tc.via_wsl
    if tc.via_vm:
        return (require(tid, vm=True), *(require(dep, vm=True) for dep in tc.requires))
    primary = require(tid, linux)
    if tc.space_free_runtime and not linux:
        primary = _space_free_runtime(primary)
    return (primary, *(require(dep, linux) for dep in tc.requires))


# ---------------------------------------------------------------------------- install

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, log) -> None:
    """The file at `url`, written beside `dest` and then moved there whole: another download of it (another
    program's) never reads half of one, nor writes into it."""
    log(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "decompnet-toolchain-installer"})
    part = dest.with_name(f"{dest.name}.{os.getpid()}-{threading.get_ident()}.part")
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def _deb_data(deb: Path, out: Path) -> Path:
    """The data.tar.* inside a Debian package (an `ar` archive), written to `out`."""
    with open(deb, "rb") as f:
        if f.read(8) != b"!<arch>\n":
            raise ToolchainError(f"{deb.name} isn't a Debian package")
        while len(head := f.read(60)) == 60:
            name, size = head[:16].decode().strip().rstrip("/"), int(head[48:58].decode().strip())
            data = f.read(size)
            if size % 2:
                f.read(1)
            if name.startswith("data.tar"):
                (out / name).write_bytes(data)
                return out / name
    raise ToolchainError(f"{deb.name} has no data.tar")


def _extract(archive: Path, dest: Path, subdir: str | None, keep: dict[str, str] | None = None) -> None:
    """Unpack an archive (or its `subdir`) into `dest`. `keep`: {path in it: where it goes in dest}, and nothing else:
    a compiler's archive is often a whole install (its IDE, libraries, the C runtime's sources: 700 MB for Visual C++
    2010) of which a build needs the compiler and headers. Two paths may go to one folder: they're merged."""
    prefix = f"{subdir.strip('/')}/" if subdir else ""
    wanted = None if keep is None else [prefix + k.strip("/") for k in keep]

    def take(name: str) -> bool:
        name = name.rstrip("/")
        return wanted is None or any(name == w or name.startswith(w + "/") for w in wanted)

    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        if archive.name.endswith(".deb"):
            with tempfile.TemporaryDirectory(dir=dest.parent) as raw:
                with tarfile.open(_deb_data(archive, Path(raw))) as t:
                    t.extractall(tmp, filter="data")
        elif archive.name.endswith(".zip"):
            import zipfile

            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    if not take(info.filename):
                        continue
                    target = (Path(tmp) / info.filename).resolve()
                    if Path(tmp).resolve() not in target.parents and target != Path(tmp).resolve():
                        raise ToolchainError(f"refusing to extract {info.filename} outside the toolchain folder")
                    z.extract(info, tmp)
                    mode = info.external_attr >> 16
                    if mode & 0o111 and not info.is_dir():
                        os.chmod(target, 0o755)
        elif archive.name.endswith(".zst"):
            # GNU tar extracts it; never restore owners from the archive. The zstd layer is undone here first: tar
            # would run the zstd command for it, which many systems don't have (tar then just fails with code 2)
            import zstandard

            plain = Path(tmp).with_name(Path(tmp).name + ".tar")
            try:
                with open(archive, "rb") as src, open(plain, "wb") as out:
                    zstandard.ZstdDecompressor().copy_stream(src, out)
                subprocess.run(["tar", "-xf", str(plain), "-C", tmp, "--no-same-owner", "--no-same-permissions"],
                               check=True, capture_output=True, creationflags=NO_WINDOW)
            finally:
                plain.unlink(missing_ok=True)
        else:
            # gz/xz in pure Python, so this works on Windows and macOS too. The "data" filter refuses
            # absolute paths, links out of the tree and device files, and drops setuid bits.
            with tarfile.open(archive) as t:
                t.extractall(tmp, members=[m for m in t.getmembers() if take(m.name)], filter="data")
        src = Path(tmp) / subdir if subdir else Path(tmp)
        if not src.is_dir() and keep is None:  # (with `keep`, the first path it doesn't have is named)
            raise ToolchainError(f"archive {archive.name} has no {subdir}/")
        for path, to in (keep or {}).items():
            item, target = src / path, dest / to
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            elif item.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
            else:
                raise ToolchainError(f"archive {archive.name} has no {path}")
        for item in src.iterdir() if keep is None else ():
            target = dest / item.name
            if target.exists():
                shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink()
            shutil.move(str(item), target)


_INSTALLING = threading.Lock()
_installs: dict[str, threading.RLock] = {}


def find_local(tc: Toolchain, folder: str | Path) -> Path | None:
    """The folder that holds a copy of a local toolchain (a kit): the one given, or where inside it the kit says
    (Kit.locate: the XDK's `xbox`, `XDK/xbox`, so the folder its installer unpacks works as it is)."""
    from . import kits

    return kits.get(tc.id).locate(Path(folder).expanduser())


def _copies(m: dict | None) -> list[str]:
    """The folders a local toolchain's manifest records: a list of copies, or (a manifest from before several copies
    were allowed) its one `folder`."""
    m = m if isinstance(m, dict) else {}
    return list(m.get("folders") or ([m["folder"]] if m.get("folder") else []))


def local_folders(tid: str) -> list[Path]:
    """Where the user's own copies of a local toolchain are (as Setup recorded them) that are still there, or a clear
    error when none is."""
    copies = _copies(installed(tid))
    if not copies:
        raise ToolchainError(f"{tid} isn't set up: in decompdecomp's Setup, under Compilers & tools, give the "
                             "folder where your own copy is")
    tc = get(tid)
    found = [Path(c) for c in copies if find_local(tc, c) is not None]
    if not found:
        raise ToolchainError(f"{tc.name} isn't at {copies[0]} any more (it needs {', '.join(tc.local_files)}): give "
                             "its folder again in Setup")
    return found


def local_folder(tid: str) -> Path:
    """The first copy of a local toolchain (local_folders)."""
    return local_folders(tid)[0]


def _link_local(tc: Toolchain, folder: str | Path | None, accept_license: bool, log) -> Path:
    """Record where a copy of the user's own local toolchain is, besides the copies recorded already. Nothing is
    copied or downloaded: decompdecomp reads it where it is, so none of it ends up in projects or in the toolchains
    folder."""
    if not folder:
        raise ToolchainError(f"{tc.name} isn't downloaded: give the folder where your own copy is")
    if tc.proprietary and not accept_license:
        raise ToolchainError(f"{tc.id} ({tc.name}) is proprietary software. Using your copy is your own licensing "
                             "decision; accept that to go on.")
    found = find_local(tc, folder)
    if found is None:
        raise ToolchainError(f"{folder} doesn't look like {tc.name}: it should hold {', '.join(tc.local_files)} "
                             "(give the folder that has them, or the one above it)")
    copies = [c for c in _copies(_manifest(tc.id)) if find_local(tc, c) is not None]
    _write_copies(tc, copies if any(Path(c) == found for c in copies) else [*copies, str(found)])
    log(f"{tc.id}: using your copy at {found}")
    return found


def _write_copies(tc: Toolchain, copies: list[str]) -> None:
    dest = path(tc.id, False)
    dest.mkdir(parents=True, exist_ok=True)
    # (`folder`, the first copy: what a decompdecomp from before several copies reads)
    _write_manifest(dest, {"id": tc.id, "name": tc.name, "license": tc.license, "source": "local",
                           "folder": copies[0], "folders": copies, "installed_at": time.time(),
                           "license_accepted": bool(tc.proprietary), "wsl": False})


def remove_local(tid: str, folder: str | Path) -> None:
    """Forget one of a local toolchain's copies (its files stay where they are); the last one forgotten leaves the
    toolchain not set up."""
    tc = get(tid)
    if not tc.local:
        raise ToolchainError(f"{tc.name} isn't a copy of your own")
    copies = _copies(_manifest(tid))
    left = [c for c in copies if Path(c) != Path(folder)]
    if len(left) == len(copies):
        raise ToolchainError(f"{folder} isn't one of your copies of {tc.name}")
    if left:
        _write_copies(tc, left)
    else:
        (path(tid, False) / MANIFEST).unlink(missing_ok=True)


def install(tid: str, *, accept_license: bool = False, force: bool = False, log=print, linux: bool | None = None, vm: bool | None = None, folder: str | Path | None = None) -> Path:
    """Install a pinned tool once at a time, or register a user's local compiler copy."""
    tc = get(tid)
    if tc.local:
        return _link_local(tc, folder, accept_license, log)
    with _INSTALLING:
        lock = _installs.setdefault(tid, threading.RLock())
    with lock:
        return _install(tid, accept_license=accept_license, force=force, log=log, linux=linux, vm=vm)


def _install(tid: str, *, accept_license: bool, force: bool, log, linux: bool | None, vm: bool | None) -> Path:
    """Install under the per-tool lock; recursive dependencies use their own locks."""
    tc = get(tid)
    vm = tc.via_vm if vm is None else vm
    linux = (tc.via_wsl or vm) if linux is None else linux
    location = {'vm': True} if vm else {}
    archive_only = vm or (linux and tc.case_sensitive)
    existing = installed(tid, linux, **location)
    if archive_only and existing and not existing.get('archive_parts'):
        force = True  # explicit reinstall upgrades old host-extracted proxy packs
    _intel_mac_runtime_ready.cache_clear()  # Rosetta may have been installed since the last setup attempt
    if not vm and (why := runtime_error(tid)):
        raise ToolchainError(why)
    if tc.proprietary and not accept_license and (force or installed(tid, linux, **location) is None):
        raise ToolchainError(
            f"{tid} ({tc.name}) is proprietary software. Running it on your coordinator is your own licensing "
            f"decision; re-run with --accept-license if you accept that.")
    if vm:
        try:
            linuxvm.setup(log=log)
        except linuxvm.VMError as error:
            raise ToolchainError(str(error)) from error
    elif linux:
        ok, why = wsl.status()
        if not ok:
            raise ToolchainError(f"{tc.name} is a Linux program: on Windows it runs in WSL 2. {why}")
    for dep in tc.requires:
        if installed(dep, linux, **location) is None or force:
            install(dep, accept_license=accept_license, force=force, log=log, linux=linux, **location)
    if installed(tid, linux, **location) and not force:
        log(f"{tid}: already installed at {path(tid, linux, **location)}")
        return path(tid, linux, **location)
    parts = tc.linux_parts() if linux else tc.parts_for()
    if not parts and not tc.git:
        why = unsupported_cpu()
        raise ToolchainError(f"{tid} ({tc.name}) has no build for this computer ({current_platform()})"
                             + (f": {why}" if why else ""))
    if tc.requires_java and jdk_version() < tc.requires_java:  # the computer has none: the pinned one runs it
        ensure("jdk", log=log)
    if tc.proprietary and not accept_license:
        raise ToolchainError(
            f"{tid} ({tc.name}) is proprietary software. Running it on your coordinator is your own licensing "
            f"decision; re-run with --accept-license if you accept that.")

    dest = path(tid, linux, **location)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    log(f"{tid}: installing {tc.name} [{tc.license}]" + (" (Linux build, runs in local VM)" if vm else " (Linux build, runs in WSL 2)" if linux else ""))
    pinned, archive_parts = [], []
    try:
        if tc.git or tc.pip_extra:
            _install_python_env(tc, dest, log)
        downloads = root() / ".downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        for part in parts:
            archive = downloads / (part.get("file") or Path(part["url"]).name)  # file: when two urls share a name
            if not archive.exists() or (part.get("sha256") and _sha256(archive) != part["sha256"]):
                _download(part["url"], archive, log)
            digest = _sha256(archive)
            if part.get("sha256") and digest != part["sha256"]:
                archive.unlink()
                raise ToolchainError(f"checksum mismatch for {part['url']}: expected {part['sha256']}, got {digest}")
            if not part.get("sha256"):
                log(f"  no pinned checksum in the registry; pinning on first use: sha256 {digest}")
            if archive_only:
                if part.get('build') or tc.build or tc.git or tc.pip_extra:
                    raise ToolchainError(f'{tid} needs a guest build recipe before it can use archive staging.')
                # macOS and Windows can collapse libc.so.1/libC.so.1 into one file.
                # Keep the verified bytes intact until the guest's case-sensitive filesystem.
                folder = dest / '.archives'
                folder.mkdir(exist_ok=True)
                suffix = ''.join(archive.suffixes)
                saved = folder / (str(len(archive_parts)) + suffix)
                shutil.copyfile(archive, saved)
                archive_parts.append({**part, 'archive': str(saved.relative_to(dest)).replace('\\', '/'), 'sha256': digest})
                pinned.append({'url': part['url'], 'sha256': digest})
                continue
            if part.get("build") == "mips-binutils":
                _build_mips_binutils(archive, dest, part.get("subdir"), log)
            elif part.get("build") == "sn64-gcc":
                _build_sn64_gcc(archive, dest, part.get("subdir"), log)
            elif part.get("build") == "gcc-papermario":
                _build_gcc_papermario(archive, dest, part.get("subdir"), log)
            elif part.get("build") in ("psx-gcc-2.6.3", "psx-gcc"):
                _build_psx_gcc(archive, dest, part, log)
                for url_key, hash_key, _ in (
                    ("patches_url", "patches_sha256", "old-gcc-patches.tar.gz"),
                    ("config_guess_url", "config_guess_sha256", "config.guess"),
                    ("config_sub_url", "config_sub_sha256", "config.sub"),
                ):
                    pinned.append({"url": part[url_key], "sha256": part[hash_key]})
            elif part.get("copy"):  # a plain file, not an archive (a data list)
                shutil.copyfile(archive, dest / part["copy"])
            elif part.get("binary"):  # a plain executable, not an archive
                shutil.copyfile(archive, dest / part["binary"])
                os.chmod(dest / part["binary"], 0o755)
            else:
                into = dest / part.get("into", "")  # a pack of several versions: each in its own folder
                into.mkdir(parents=True, exist_ok=True)
                _extract(archive, into, part.get("subdir"), part.get("keep"))
                for folder in part.get("mkdir", ()):  # the POSIX root of an MSYS2 program needs them (#130)
                    (into / folder).mkdir(parents=True, exist_ok=True)
                if part.get("executable"):  # archives that don't record the executable bit
                    for f in dest.rglob("*"):
                        if f.is_file():
                            os.chmod(f, 0o755)
                if part.get("build") == "ghidra-decompiler":
                    _build_ghidra_decompiler(into, log)
            pinned.append({"url": part["url"], "sha256": digest})
        for into in dict.fromkeys(part.get("into", "") for part in parts) if tc.headers else ():
            folder = dest / into
            lines = [Path(os.path.relpath(path(h, linux, **location) / "Include", folder)).as_posix()
                     for h in tc.headers]
            (folder / INCLUDES).write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        if tc.build == "psyq-obj-parser":
            _build_psyq_parser(dest, linux, log)
        if tc.build == "torch":
            _build_torch(dest, log)
        if linux and not archive_only and "runtime-i386" in tc.requires:  # WSL systems rarely have a 32-bit loader: bring ours
            n = wsl.wrap_i386(dest, wsl.path(path("runtime-i386", linux=True)))
            log(f"  {n} 32-bit programs start through the pinned 32-bit runtime")
        if tc.wine and not linux:
            from . import wine

            if wine.needed():  # its prefix now, not in the middle of the first build (wineboot takes up to a minute)
                try:
                    wine.prepare(log=log)
                except RuntimeError as error:  # (not "install wine" later: wine is here, its prefix failed)
                    raise ToolchainError(f"{tc.name} downloaded, but {error}") from None
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    _write_manifest(dest, {
        "id": tid, "name": tc.name, "license": tc.license, "source": tc.source,
        "parts": pinned, "commit": tc.commit or None, "installed_at": time.time(),
        "license_accepted": bool(tc.proprietary and accept_license), "wsl": linux and not vm, "linux_vm": vm,
        "archive_parts": archive_parts, "fingerprint": fingerprint(tc, linux),
    })
    log(f"{tid}: installed at {dest}")
    return dest


def _build_psyq_parser(dest: Path, linux: bool, log) -> None:
    """Build only PCSX-Redux's standalone converter and its pinned header dependencies."""
    source = "source"
    argv = ["c++", "-std=c++20", "-O2", "-DNDEBUG", "-DFMT_HEADER_ONLY",
            *["-I" + source + "/" + p for p in ("", "pcsx/src", "fmt/include", "elfio", "args", "magic_enum/include")],
            *[source + "/pcsx/" + p for p in ("tools/psyq-obj-parser/psyq-obj-parser.cc",
                                             "src/supportpsx/psyq-obj.cc", "src/support/file.cc")],
            "-lz", "-o", "psyq-obj-parser"]
    log("  building the pinned PsyQ-to-ELF converter (first install only)")
    try:
        if linux and sys.platform == "win32":
            result = wsl.run(["prlimit", "--as=2147483648", "--", *argv], dest, 300,
                             inputs=[source], outputs=["psyq-obj-parser"])
        else:
            result = subprocess.run(limits.capped(argv), cwd=dest, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=300, creationflags=NO_WINDOW)
        if result.returncode or not (dest / "psyq-obj-parser").is_file():
            raise ToolchainError("Building the PsyQ converter needs a C++20 compiler and zlib headers "
                                 "(Xcode Command Line Tools on macOS; g++ and zlib1g-dev on Linux/WSL).\n"
                                 + (result.stdout + result.stderr)[-3000:])
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolchainError(f"Couldn't build the PsyQ converter: {error}. Install a C++20 compiler and zlib headers.") from error


# Torch's CMakeLists fetches these at configure time (FetchContent); each is a pinned part of [torch] instead, handed
# to CMake as an unpacked folder so the build downloads nothing and every computer builds the same sources.
_TORCH_DEPS = ("dr_libs", "libgfxd", "spdlog", "tinyxml2", "yaml-cpp", "zlib")


def _build_torch(dest: Path, log) -> None:
    """Build Torch from its pinned source with the pinned CMake (its standalone command, no viewer). Only the host's
    C++ compiler, make and zlib headers come from the system, as for the PsyQ converter."""
    mac = current_platform().startswith("macos")
    tools = ("Torch is built from its source on this computer, which needs "
             + ("Xcode Command Line Tools: run `xcode-select --install`, then try again." if mac else
                "a C++17 compiler, make and zlib headers (on Debian or Ubuntu: `sudo apt install g++ make zlib1g-dev`), "
                "then try again."))
    if not shutil.which("make") or not shutil.which("c++"):
        raise ToolchainError(tools)
    source = dest / "source"
    cmake = str(binary("cmake"))
    log("  building Torch from its pinned source (first install only, a few minutes)")
    with tempfile.TemporaryDirectory(prefix="decomp-torch-") as tmp:
        build = Path(tmp) / "build"
        configure = [cmake, "-S", str(source / "torch"), "-B", str(build), "-G", "Unix Makefiles",
                     "-DCMAKE_BUILD_TYPE=Release", "-DUSE_STANDALONE=ON", "-DBUILD_UI=OFF",
                     "-DFETCHCONTENT_FULLY_DISCONNECTED=ON", "-DCMAKE_DISABLE_FIND_PACKAGE_spdlog=ON",
                     *(f"-DFETCHCONTENT_SOURCE_DIR_{name.upper()}={source / 'deps' / name}" for name in _TORCH_DEPS)]
        steps = (configure, [cmake, "--build", str(build), "--parallel", str(min(8, os.cpu_count() or 1))])
        for argv in steps:
            try:
                result = subprocess.run(limits.capped(argv), cwd=tmp, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=1800, creationflags=NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ToolchainError(f"Couldn't build Torch: {error}. {tools}") from error
            if result.returncode:
                raise ToolchainError(f"Building Torch failed. {tools}\n" + (result.stdout + result.stderr)[-3000:])
        built = build / "torch"
        if not built.is_file():
            raise ToolchainError(f"Torch's build finished without a torch program in {build}. {tools}")
        shutil.copyfile(built, dest / "torch")
        os.chmod(dest / "torch", 0o755)
    shutil.rmtree(source, ignore_errors=True)  # the program is what's kept; the pinned archives stay in .downloads


def _build_ghidra_decompiler(ghidra: Path, log) -> None:
    """Build Ghidra's decompiler from the C++ source its release ships, where the release has none for this computer
    (macOS, ARM Linux). Only the host build tools (a C++ compiler and make) come from the system. Its Makefile builds
    for Intel on every Mac (ARCH_TYPE) and for macOS 10.6 (ADDITIONAL_FLAGS): both are left out, so it builds for
    this computer."""
    target = ghidra_decompiler(ghidra)
    if target.is_file():
        return
    mac = current_platform().startswith("macos")
    tools = ("Ghidra's decompiler is built from its source on this computer, which needs "
             + ("Xcode Command Line Tools: run `xcode-select --install`, then try again." if mac else
                "a C++ compiler and make (on Debian or Ubuntu: `sudo apt install g++ make`), then try again."))
    if not shutil.which("make") or not shutil.which("c++"):
        raise ToolchainError(tools)
    with tempfile.TemporaryDirectory(prefix="decomp-ghidra-") as tmp:
        build = Path(tmp) / "decompile"
        shutil.copytree(ghidra / "Ghidra" / "Features" / "Decompiler" / "src" / "decompile", build)
        argv = ["make", "-j" + str(min(4, os.cpu_count() or 1)), "ghidra_opt", "ARCH_TYPE=", "ADDITIONAL_FLAGS=-w",
                "CXX=c++ -std=c++11"]
        log("  building Ghidra's decompiler for this computer (first install only, a few minutes)")
        try:
            res = subprocess.run(limits.capped(argv), cwd=build / "cpp", capture_output=True, text=True,
                                 errors="replace", timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ToolchainError(f"Couldn't build Ghidra's decompiler: {error}. {tools}") from error
        built = build / "cpp" / "ghidra_opt"
        if res.returncode or not built.is_file():
            raise ToolchainError(f"Couldn't build Ghidra's decompiler. {tools}\n" + (res.stdout + res.stderr)[-4000:])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, target)
        target.chmod(0o755)


def _build_mips_binutils(archive: Path, dest: Path, subdir: str | None, log) -> None:
    """The v0.10 upstream has no Intel Mac archive. Build that same pinned source, never a tool from PATH.
    Only the host build tools (Xcode's C compiler and make) come from the system; installed MIPS tools stay pinned.
    """
    if not shutil.which("make") or not shutil.which("clang"):
        raise ToolchainError("Building MIPS binutils on Intel macOS needs Xcode Command Line Tools: run `xcode-select --install`, then try again.")
    with tempfile.TemporaryDirectory(prefix="decomp-binutils-") as tmp:
        source = Path(tmp) / "source"
        build = Path(tmp) / "build"
        source.mkdir()
        build.mkdir()
        (build / "gas" / "doc").mkdir(parents=True)  # git sources omit the generated documentation directory
        _extract(archive, source, subdir)
        # The git archive includes GDB, unlike a standalone binutils release. Its top-level configure probes
        # GMP/MPFR whenever gdb/ exists, even with --disable-gdb. We build only the assembler/linker/object tools.
        shutil.rmtree(source / "gdb", ignore_errors=True)
        shutil.rmtree(source / "gdbserver", ignore_errors=True)
        commands = [
            [str(source / "configure"), "--target=mips", "--disable-gprofng", "--disable-gdb", "--disable-werror",
             "--disable-nls", "--disable-sim", "--without-zstd", "--with-system-zlib"],
            ["make", "-j" + str(min(4, os.cpu_count() or 1)), "all-binutils", "all-gas", "all-ld", "MAKEINFO=true"],
        ]
        for argv in commands:
            log("  building pinned MIPS binutils: " + Path(argv[0]).name + " (first install only)")
            try:
                res = subprocess.run(limits.capped(argv), cwd=build, capture_output=True, text=True,
                                     errors="replace", timeout=900, env=dict(os.environ, CC="/usr/bin/clang", MAKEINFO="true"))
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ToolchainError(f"Couldn't build the pinned MIPS tools: {error}. Install Xcode Command Line Tools and try again.") from error
            if res.returncode:
                raise ToolchainError("Couldn't build the pinned MIPS tools. Install Xcode Command Line Tools and try again.\n"
                                     + (res.stdout + res.stderr)[-4000:])
        outputs = {"as": "gas/as-new", "ld": "ld/ld-new", "objcopy": "binutils/objcopy",
                   "objdump": "binutils/objdump", "nm": "binutils/nm-new", "ar": "binutils/ar",
                   "ranlib": "binutils/ranlib", "strip": "binutils/strip-new", "readelf": "binutils/readelf"}
        for name, binary in outputs.items():
            target = dest / ("mips-ps2-decompals-" + name)
            if not (build / binary).is_file():
                raise ToolchainError(f"The pinned MIPS binutils build did not produce {name} ({binary}). "
                                     "Check Xcode Command Line Tools and try again.")
            shutil.copy2(build / binary, target)
            target.chmod(0o755)


# Apple arm64 passes variadic arguments on the stack but fixed ones in registers, so a variadic function called
# with no prototype in scope reads garbage: GCC 2.8 calls its diagnostics that way in about 40 files, and every
# compile error crashed (a segfault in strlen) where the Intel build exits 33. Forced into each host file, these
# prototypes give every call the variadic convention. The drivers (gcc.c, g++.c, built with STANDARD_EXEC_PREFIX)
# and cccp.c (GCC_INCLUDE_DIR) define their own static versions, so they are left alone.
_GCC_PM_VARARGS = """\
#ifndef DECOMP_ARM64_VARARGS_H
#define DECOMP_ARM64_VARARGS_H
#if !defined (STANDARD_EXEC_PREFIX) && !defined (GCC_INCLUDE_DIR)
union tree_node; struct rtx_def;
extern void error (char *, ...);
extern void warning (char *, ...);
extern void pedwarn (char *, ...);
extern void fatal (char *, ...);
extern void sorry (char *, ...);
extern void really_sorry (char *, ...);
extern void error_with_decl (union tree_node *, char *, ...);
extern void warning_with_decl (union tree_node *, char *, ...);
extern void pedwarn_with_decl (union tree_node *, char *, ...);
extern void error_with_file_and_line (char *, int, char *, ...);
extern void warning_with_file_and_line (char *, int, char *, ...);
extern void pedwarn_with_file_and_line (char *, int, char *, ...);
extern void error_for_asm (struct rtx_def *, char *, ...);
extern void warning_for_asm (struct rtx_def *, char *, ...);
#endif
#endif
"""

# Old-style declarations that conflict with the prototypes above, removed exactly (the build fails closed when
# the pinned source no longer has them).
_GCC_PM_DECLARATIONS = {
    "config/mips/mips.c": ("extern void   error ();\n", "extern void   fatal ();\n", "extern void   warning ();\n"),
    "cp/errfn.c": ("  extern errorfn error;\n", "  extern errorfn warning;\n", "  extern errorfn pedwarn;\n",
                   "  extern errorfn error_with_file_and_line;\n", "  extern errorfn warning_with_file_and_line;\n",
                   "  extern errorfn pedwarn_with_file_and_line;\n"),
}


def _build_gcc_papermario(archive: Path, dest: Path, subdir: str | None, log) -> None:
    """Build the checksum-pinned gcc-papermario (GCC 2.8.1) natively for Apple Silicon.

    Its released Mac build is an Intel program: under Rosetta each launch costs 30-40 ms and one `gcc -S` about
    230 ms, against 13 ms native, and the permuter and drafts start it thousands of times. Two things make the
    native build produce the Intel build's bytes (docs/COMPILER_MATCHING_LEARNINGS.md has the comparison):
    no optimisation of GCC itself, as upstream's CI builds it (clang -O2 miscompiles its float constant folding:
    a 4294967296.0 came out as the wrong words), and the variadic prototypes above.
    """
    if not shutil.which("/usr/bin/clang") or not shutil.which("/usr/bin/make"):
        raise ToolchainError("Building GCC 2.8.1 (PM matching) needs Xcode Command Line Tools: "
                             "run `xcode-select --install`, then try again.")
    with tempfile.TemporaryDirectory(prefix="decomp-gcc-pm-") as tmp:
        source = Path(tmp) / "source"
        source.mkdir()
        _extract(archive, source, subdir)
        for name, lines in _GCC_PM_DECLARATIONS.items():
            file = source / name
            text = file.read_text(encoding="latin-1") if file.is_file() else ""
            for line in lines:
                if text.count(line) != 1:
                    raise ToolchainError(f"The pinned gcc-papermario {name} no longer matches its arm64 fix.")
                text = text.replace(line, "", 1)
            file.write_text(text, encoding="latin-1")
        header = source / "decomp-arm64-varargs.h"
        header.write_text(_GCC_PM_VARARGS, encoding="latin-1")
        # An archive gives every file one time, and make would then regenerate the shipped parsers with whatever
        # bison the computer has (a modern one makes a different parser, which doesn't build). Sources older,
        # the shipped generated files newer.
        now = time.time()
        for file in source.rglob("*"):
            if file.is_file():
                os.utime(file, (now - 60, now - 60))
        for name in ("c-parse.c", "c-parse.h", "cexp.c", "cp/parse.c", "cp/parse.h"):
            if not (source / name).is_file():
                raise ToolchainError(f"The pinned gcc-papermario source has no generated {name}.")
            os.utime(source / name, (now, now))

        quiet = ("-std=gnu89 -Wno-return-type -Wno-error -Wno-error=implicit-function-declaration "
                 "-Wno-implicit-function-declaration -Wno-implicit-int -Wno-int-conversion "
                 "-Wno-incompatible-pointer-types -Wno-deprecated-non-prototype")
        env = {k: v for k, v in os.environ.items() if k not in ("CC", "CXX", "CFLAGS", "CPPFLAGS", "LDFLAGS")}
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        commands = [
            ["./configure", "--target=mips-nintendo-nu64", "--prefix=/opt/cross", "--with-gnu-as",
             "--disable-gprof", "--disable-gdb", "--disable-werror",
             "--host=i386-apple-darwin", "--build=i386-apple-darwin"],
            ["/usr/bin/make", "-j" + str(min(6, os.cpu_count() or 1)), "cpp", "cc1", "xgcc", "cc1plus", "g++",
             f"CFLAGS={quiet} -include {header}", f"HOST_CFLAGS={quiet} -DHAVE_CONFIG_H -DNU64"],
        ]
        for argv in commands:
            log("  building native GCC 2.8.1 (PM matching) for Apple Silicon (first install only): "
                + Path(argv[0]).name)
            try:
                res = subprocess.run(limits.capped(argv), cwd=source, capture_output=True, text=True,
                                     errors="replace", timeout=900, env=dict(env, CC="/usr/bin/clang", CFLAGS=quiet))
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ToolchainError(f"Couldn't build GCC 2.8.1 (PM matching): {error}. "
                                     "Check Xcode Command Line Tools and try again.") from error
            if res.returncode:
                raise ToolchainError("Couldn't build GCC 2.8.1 (PM matching). Check Xcode Command Line Tools "
                                     "and try again.\n" + (res.stdout + res.stderr)[-4000:])
        _check_gcc_papermario(source, Path(tmp))
        for name, binary in {"cpp": "cpp", "cc1": "cc1", "gcc": "xgcc", "cc1plus": "cc1plus", "g++": "g++"}.items():
            if not (source / binary).is_file():
                raise ToolchainError(f"The GCC 2.8.1 (PM matching) build did not produce {name} ({binary}).")
            shutil.copy2(source / binary, dest / name)
            (dest / name).chmod(0o755)


def _check_gcc_papermario(build: Path, tmp: Path) -> None:
    """The two ways a native build went wrong, checked before it is installed: a float constant folded to the
    wrong words, and a compile error that crashes instead of being reported."""
    floats, broken = tmp / "floats.c", tmp / "broken.c"
    floats.write_text("double f(unsigned int x) { return x; }\n", encoding="utf-8")
    broken.write_text("int f(void) { return undeclared; }\n", encoding="utf-8")
    runs = {}
    for src in (floats, broken):
        try:
            runs[src] = subprocess.run([str(build / "cc1"), "-quiet", "-O2", "-G0", "-mips3", str(src),
                                        "-o", str(src.with_suffix(".s"))],
                                       capture_output=True, text=True, errors="replace", timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ToolchainError(f"The native GCC 2.8.1 (PM matching) failed its check: {error}") from error
    asm = floats.with_suffix(".s").read_text(errors="replace") if floats.with_suffix(".s").is_file() else ""
    if runs[floats].returncode or not re.search(r"li\.d\s+\$f\d+,4\.29496729600000000000e9", asm):
        raise ToolchainError("The native GCC 2.8.1 (PM matching) folds float constants wrongly; not installing it.\n"
                             + runs[floats].stderr[-2000:] + asm[-2000:])
    if runs[broken].returncode != 33 or "undeclared" not in runs[broken].stderr:
        raise ToolchainError("The native GCC 2.8.1 (PM matching) doesn't report compile errors (exit "
                             f"{runs[broken].returncode}); not installing it.\n" + runs[broken].stderr[-2000:])


# GCC 2.7.2 keeps a CONST_DOUBLE's value as consecutive HOST_WIDE_INTs from CONST_DOUBLE_LOW and copies it with
# bcopy. The pinned source is configured for a 32-bit host (config/i386/xm-gnu.h), so a HOST_WIDE_INT is 32 bits, but
# on a 64-bit Mac each rtx operand slot is 8: a double went into LOW's slot whole and CONST_DOUBLE_HIGH was never
# written. rtx_alloc doesn't clear operands, so whatever the obstack held there decided whether a float constant is
# `li.s` or a load from .rodata, and it changed from run to run. These helpers copy word by word, laid out as the
# 32-bit Linux release has them; each bcopy below is replaced exactly, or the build fails closed.
_SN64_CONST_DOUBLE = """
/* decompdecomp: copy a CONST_DOUBLE's value one HOST_WIDE_INT per operand slot, as a 32-bit host lays it out.  */
static void decomp_cd_get (struct rtx_def *x, char *to, int size) __attribute__ ((unused));
static void decomp_cd_put (struct rtx_def *x, char *from, int size) __attribute__ ((unused));
static int decomp_cd_cmp (struct rtx_def *x, char *with, int size) __attribute__ ((unused));
static void decomp_cd_get (struct rtx_def *x, char *to, int size)
{ int i; for (i = 0; i * (int) sizeof (HOST_WIDE_INT) < size; i++)
    bcopy ((char *) &XWINT (x, 2 + i), to + i * sizeof (HOST_WIDE_INT), sizeof (HOST_WIDE_INT)); }
static void decomp_cd_put (struct rtx_def *x, char *from, int size)
{ int i; for (i = 0; i * (int) sizeof (HOST_WIDE_INT) < size; i++)
    bcopy (from + i * sizeof (HOST_WIDE_INT), (char *) &XWINT (x, 2 + i), sizeof (HOST_WIDE_INT)); }
static int decomp_cd_cmp (struct rtx_def *x, char *with, int size)
{ int i; for (i = 0; i * (int) sizeof (HOST_WIDE_INT) < size; i++)
    if (bcmp ((char *) &XWINT (x, 2 + i), with + i * sizeof (HOST_WIDE_INT), sizeof (HOST_WIDE_INT))) return 1;
  return 0; }
"""

# (file, exact text, replacement): the CONST_DOUBLE copies above, the old-style declarations that conflict with the
# variadic prototypes (_GCC_PM_VARARGS), and prototypes for the two other variadic functions called without one.
_SN64_EDITS = (
    ("rtl.h", "extern int rtx_to_tree_code\tPROTO((enum rtx_code));\n",
     "extern int rtx_to_tree_code\tPROTO((enum rtx_code));\n" + _SN64_CONST_DOUBLE),
    ("real.h", "bcopy ((char *) &CONST_DOUBLE_LOW ((from)), (char *) &u, sizeof u);",
     "decomp_cd_get ((from), (char *) &u, sizeof u);"),
    ("emit-rtl.c", "bcopy ((char *) &u, (char *) &CONST_DOUBLE_LOW (tem), sizeof u);",
     "decomp_cd_put (tem, (char *) &u, sizeof u);"),
    ("dwarf2out.c", "bcopy ((char *) &CONST_DOUBLE_LOW (rtl), (char *) &u, sizeof u);",
     "decomp_cd_get (rtl, (char *) &u, sizeof u);"),
    ("varasm.c", "! bcmp ((char *) &CONST_DOUBLE_LOW (r), (char *) &u, sizeof u)",
     "! decomp_cd_cmp (r, (char *) &u, sizeof u)"),
    ("varasm.c", "bcopy ((char *) &u, (char *) &CONST_DOUBLE_LOW (r), sizeof u);",
     "decomp_cd_put (r, (char *) &u, sizeof u);"),
    ("varasm.c", "bcopy ((char *) &CONST_DOUBLE_LOW (x),\n\t\t (char *) &value->un.du, sizeof value->un.du);",
     "decomp_cd_get (x, (char *) &value->un.du, sizeof value->un.du);"),
    ("varasm.c", "bcopy ((char *) &CONST_DOUBLE_LOW (x), (char *) &u, sizeof u);\n\t  assemble_real",
     "decomp_cd_get (x, (char *) &u, sizeof u);\n\t  assemble_real"),
    ("config/mips/mips.c", "extern void   error ();\n", ""),
    ("config/mips/mips.c", "extern void   fatal ();\n", ""),
    ("config/mips/mips.c", "extern void   warning ();\n", ""),
    ("profile.c", '#include "gcov-io.h"\n',
     '#include "gcov-io.h"\nextern void emit_library_call (struct rtx_def *, int, enum machine_mode, int, ...);\n'),
    ("bc-optab.h", "extern struct increment_operator optab_postincrement_expr[];\n",
     "extern struct increment_operator optab_postincrement_expr[];\n"
     "extern void bc_emit_instruction (enum bytecode_opcode, ...);\n"),
)


def _build_sn64_gcc(archive: Path, dest: Path, subdir: str | None, log) -> None:
    """Build the checksum-pinned reconstructed SN compiler (GCC 2.7.2) natively on macOS.

    Upstream's release is a 32-bit Linux program; built as it comes for a 64-bit Mac it crashed on most compile
    errors and printed different code for one input from run to run. The variadic prototypes (Apple arm64) and the
    CONST_DOUBLE copies (any 64-bit host) above make it print the Linux release's code:
    docs/COMPILER_MATCHING_LEARNINGS.md, "Reconstructed SN64 GCC 2.7.2 on 64-bit Macs", has the comparison.
    """
    if not shutil.which("/usr/bin/clang") or not shutil.which("/usr/bin/make"):
        raise ToolchainError("Building reconstructed SN64 GCC needs Xcode Command Line Tools: "
                             "run `xcode-select --install`, then try again.")
    with tempfile.TemporaryDirectory(prefix="decomp-sn64-gcc-") as tmp:
        source = Path(tmp) / "source"
        source.mkdir()
        _extract(archive, source, subdir)
        texts: dict[str, str] = {}
        for name, old, new in _SN64_EDITS:
            if name not in texts:
                file = source / name
                texts[name] = file.read_text(encoding="latin-1") if file.is_file() else ""
            if texts[name].count(old) != 1:
                raise ToolchainError(f"The pinned reconstructed SN64 GCC {name} no longer matches its 64-bit fix.")
            texts[name] = texts[name].replace(old, new, 1)
        # Old GCC's bundled assert.h expects a libgcc host helper absent from Apple's
        # runtime. Supply that diagnostic routine only; target codegen is untouched.
        host = source / "getpwd.c"
        texts["getpwd.c"] = host.read_text(encoding="latin-1") + (
            '\n#include <stdio.h>\nvoid __eprintf(const char *fmt, const char *expr, unsigned int line, '
            'const char *file) { fprintf(stderr, fmt, expr, line, file); abort(); }\n')
        for name, text in texts.items():
            (source / name).write_text(text, encoding="latin-1")
        header = source / "decomp-arm64-varargs.h"
        header.write_text(_GCC_PM_VARARGS, encoding="latin-1")
        quiet = ("-DDARWIN -std=gnu89 -DHAVE_VPRINTF -Wno-return-type -Wno-error -Wno-implicit-function-declaration "
                 "-Wno-int-conversion -Wno-incompatible-pointer-types -Wno-deprecated-non-prototype")
        env = {k: v for k, v in os.environ.items() if k not in ("CC", "CXX", "CFLAGS", "CPPFLAGS", "LDFLAGS")}
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"  # the shipped parsers, never a computer's newer bison
        # The generators (gen*.c) declare their own non-variadic fatal(), so the prototypes go to cc1 and cpp only.
        argv = ["/usr/bin/make", "-j" + str(min(6, os.cpu_count() or 1)), "cpp", "cc1", "CC=/usr/bin/clang",
                f"CFLAGS={quiet} {SN64_GCC_OPT} -include {header}", f"HOST_CFLAGS={quiet} {SN64_GCC_OPT}"]
        log("  building pinned reconstructed SN64 GCC (first install only)")
        try:
            result = subprocess.run(limits.capped(argv), cwd=source, capture_output=True, text=True,
                                    errors="replace", timeout=900, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ToolchainError(f"Couldn't build reconstructed SN64 GCC: {e}. Check Xcode Command Line Tools and try again.") from e
        if result.returncode:
            raise ToolchainError("Couldn't build reconstructed SN64 GCC.\n" + (result.stdout + result.stderr)[-4000:])
        for name in ("cpp", "cc1"):
            if not (source / name).is_file():
                raise ToolchainError(f"The reconstructed SN64 GCC build did not produce {name}.")
        _check_sn64_gcc(source, Path(tmp))
        for name in ("cpp", "cc1"):
            shutil.copyfile(source / name, dest / name)
            (dest / name).chmod(0o755)


# clang -O2 miscompiles real.c's type punning (4294967296.0 came out as the words 0, 0x41f0), likely what broke GCC
# 2.8.1 (PM matching) at -O2 too. With strict aliasing off it prints the Linux release's code on the whole corpus, as
# an unoptimised build does, and spends about a third less CPU a compile.
SN64_GCC_OPT = "-O2 -fno-strict-aliasing"
# What the Linux release prints for _SN64_FLOATS at the profile's flags (run under qemu-i386): a broken 64-bit build
# loaded these constants from .rodata instead, or printed a different listing on the next run.
_SN64_FLOATS = ("float g(float x) { return x * 1.5f + 0.1f; }\n"
                "double f(unsigned int x) { return x; }\n"
                "float t[] = { -1000.0f, 0.5f };\n")
_SN64_EXPECTED = (r"li\.s\s+\$f0,1\.50000000000000000000e0\n", r"li\.s\s+\$f0,1\.00000001490116119385e-1\n",
                  r"li\.d\s+\$f0,4\.29496729600000000000e9\n", r"\.word\s+0xc47a0000\t\t# -1000 \(float\)\n",
                  r"\.word\s+0x3f000000\t\t# 0\.5 \(float\)\n")


def _check_sn64_gcc(build: Path, tmp: Path) -> None:
    """The ways a native build went wrong, checked before it is installed: float constants that aren't the Linux
    release's, a listing that changes from run to run, and a compile error that crashes instead of being reported."""
    floats, broken = tmp / "floats.c", tmp / "broken.c"
    floats.write_text(_SN64_FLOATS, encoding="utf-8")
    broken.write_text("int f(void) { return undeclared; }\n", encoding="utf-8")
    flags = ["-quiet", "-G0", "-mips3", "-O2", "-mgas", "-meb", "-mhard-float", "-fno-exceptions"]
    listings, runs = [], {}
    for src, out in ((floats, "a.s"), (floats, "b.s"), (floats, "c.s"), (broken, "broken.s")):
        try:
            runs[src] = subprocess.run([str(build / "cc1"), *flags, str(src), "-o", str(tmp / out)],
                                       capture_output=True, text=True, errors="replace", timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ToolchainError(f"The native reconstructed SN64 GCC failed its check: {error}") from error
        if src == floats:
            listings.append((tmp / out).read_text(errors="replace") if not runs[src].returncode and
                            (tmp / out).is_file() else "")
    asm = listings[0]
    if not asm or not all(re.search(pattern, asm) for pattern in _SN64_EXPECTED):
        raise ToolchainError("The native reconstructed SN64 GCC prints float constants wrongly; not installing it.\n"
                             + runs[floats].stderr[-2000:] + asm[-2000:])
    if len(set(listings)) != 1:
        raise ToolchainError("The native reconstructed SN64 GCC prints different code for the same file from run "
                             "to run; not installing it.")
    if runs[broken].returncode != 33 or "undeclared" not in runs[broken].stderr:
        raise ToolchainError("The native reconstructed SN64 GCC doesn't report compile errors (exit "
                             f"{runs[broken].returncode}); not installing it.\n" + runs[broken].stderr[-2000:])


def _build_psx_gcc(archive: Path, dest: Path, part: dict, log) -> None:
    """Build a pinned GPL PlayStation GCC natively for this Mac.

    The only host tools are Xcode's clang and make. GCC source and the old-gcc patch bundle,
    plus modern config.guess/config.sub, are checksum-pinned by this toolchain part.
    """
    version = part.get("version", "2.6.3")
    if not shutil.which("/usr/bin/clang") or not shutil.which("/usr/bin/make"):
        raise ToolchainError(
            f"Building GCC {version} for PlayStation needs Xcode Command Line Tools. "
            "Run `xcode-select --install`, then try again."
        )
    dest.mkdir(parents=True, exist_ok=True)
    build_inputs = (
        ("patches_url", "patches_sha256", "old-gcc-patches.tar.gz"),
        ("config_guess_url", "config_guess_sha256", "config.guess"),
        ("config_sub_url", "config_sub_sha256", "config.sub"),
    )
    with tempfile.TemporaryDirectory(prefix="decomp-psx-gcc-") as tmp:
        root = Path(tmp)
        source_root = root / "source"
        source_root.mkdir()
        _extract(archive, source_root, None)
        source = source_root / f"gcc-{version}"
        if not source.is_dir():
            raise ToolchainError(f"The pinned GCC {version} archive has no gcc-{version} source directory.")

        downloads = root / "inputs"
        downloads.mkdir()
        inputs = {}
        for url_key, hash_key, filename in build_inputs:
            path = downloads / filename
            _download(part[url_key], path, log)
            actual = _sha256(path)
            if actual != part[hash_key]:
                raise ToolchainError(f"checksum mismatch for {part[url_key]}: expected {part[hash_key]}, got {actual}")
            inputs[filename] = path

        patch_root = root / "old-gcc"
        patch_root.mkdir()
        _extract(inputs["old-gcc-patches.tar.gz"], patch_root, None)
        old_gcc = next(patch_root.glob("old-gcc-*/"), None)
        if old_gcc is None:
            raise ToolchainError("The pinned old-gcc patch archive has no repository directory.")
        patches = old_gcc / "patches"

        env = dict(os.environ)
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:" + env.get("PATH", "")
        env.pop("CC", None)
        env.pop("CXX", None)
        for source_file in source.glob("*.c"):
            source_file.chmod(source_file.stat().st_mode | 0o200)
            text = source_file.read_text(encoding="latin-1")
            if "include <varargs.h>" in text:
                source_file.write_text(text.replace("include <varargs.h>", "include <stdarg.h>"), encoding="latin-1")

        recipes = {
            "2.6.0": (("obstack.h", "obstack-2.7.2.h.patch"), ("collect2.c", "collect2-2.6.0.c.patch"),
                      ("cccp.c", "cccp-2.6.0.c.patch"), ("gcc.c", "gcc-2.6.0.c.patch"),
                      ("cp/g++.c", "g++-2.6.0.c.patch"), ("config/mips/mips.h", "mipsel-2.6.patch")),
            "2.6.3": (("obstack.h", "obstack-2.7.2.h.patch"), ("cp/g++.c", "g++-2.6.3.c.patch")),
            "2.8.0": (("obstack.h", "obstack-2.8.0.h.patch"), ("config/mips/mips.h", "mips.patch")),
        }
        if version not in recipes:
            raise ToolchainError(f"No native PS1 GCC build recipe for {version}.")
        commands = []
        for target, patch in recipes[version]:
            file = source / target
            file.chmod(file.stat().st_mode | 0o200)
            commands.append(["/usr/bin/patch", "-f", "-u", target, "-i", str(patches / patch)])
        target_patch = "psx.patch" if version == "2.8.0" else "psx-2.5.7.patch"
        commands.append(["/usr/bin/patch", "-f", "-su", "-p1", "-i", str(patches / target_patch)])
        for argv in commands:
            if not Path(argv[-1]).is_file():
                raise ToolchainError(f"The pinned old-gcc archive is missing {Path(argv[-1]).name}.")
            log("  applying pinned PlayStation GCC patch " + Path(argv[-1]).name)
            res = subprocess.run(argv, cwd=source, capture_output=True, text=True, errors="replace",
                                 timeout=60, env=env)
            if res.returncode:
                raise ToolchainError(f"Couldn't apply {Path(argv[-1]).name}:\n{(res.stdout + res.stderr)[-2000:]}")

        # The upstream sdbout patch's context is one line off in the released 2.6.3 tarball; make the same
        # portability change directly and fail closed if upstream changes the source.
        if version in ("2.6.0", "2.6.3"):
            sdbout = source / "sdbout.c"
            sdb = sdbout.read_text(encoding="latin-1")
            old_guard = "#if defined(USG) && !defined(MIPS) && !defined (hpux)"
            if version == "2.6.3":
                old_guard += " && !defined(WINNT)"
            new_guard = "#if defined(USG) && !defined(MIPS) && !defined (hpux) && !defined(_WIN32) && !defined(__linux__)"
            if old_guard not in sdb:
                raise ToolchainError(f"GCC {version} sdbout.c no longer matches the pinned portability patch.")
            sdbout.write_text(sdb.replace(old_guard, new_guard, 1), encoding="latin-1")

        for name in ("gcc.c", "cp/g++.c", "g++.c", "cccp.c"):
            if not (source / name).exists():
                continue
            path = source / name
            path.chmod(path.stat().st_mode | 0o200)
            text = path.read_text(encoding="latin-1")
            text = text.replace("extern int sys_nerr;", "extern const int sys_nerr;")
            text = text.replace("extern char *sys_errlist[];", "extern const char *const sys_errlist[];")
            path.write_text(text, encoding="latin-1")

        # The upstream PSX configure stanza assigns three DEFS words without quoting them, so dash treats
        # the second word as a command. Keep the complete preprocessor definition string together.
        configure = source / "configure"
        config_text = configure.read_text(encoding="latin-1")
        target_defs = "DEFS=-Dpsx -D__psx__ -D__psx"
        if version in ("2.6.0", "2.6.3") and target_defs not in config_text:
            raise ToolchainError(f"GCC {version} configure no longer has the expected PSX target stanza.")
        config_text = config_text.replace(target_defs, 'DEFS="-Dpsx -D__psx__ -D__psx"', 1)

        # These GCC releases' config scripts predate Apple Silicon and do not recognize Sony's PSX target OS.
        for filename, destination in (("config.guess", source / "config.guess"),
                                      ("config.sub", source / "config.sub")):
            destination.chmod(destination.stat().st_mode | 0o200)
            shutil.copyfile(inputs[filename], destination)
            destination.chmod(0o755)
        config_sub = (source / "config.sub").read_text(encoding="latin-1")
        marker = "| hiux* | abug | nacl*"
        if marker not in config_sub:
            raise ToolchainError("The pinned config.sub no longer has the expected OS list.")
        (source / "config.sub").write_text(config_sub.replace(marker, "| psx* \\\n\t     " + marker, 1), encoding="latin-1")

        # Teach this GCC release's hand-written configure script the native Darwin host description.
        case = "\t*)\n\t\techo \"Configuration $machine not supported\" 1>&2"
        if case not in config_text:
            raise ToolchainError(f"GCC {version} configure no longer has the expected host fallback.")
        config_text = config_text.replace(
            case,
            "\t\t*-apple-darwin*)\n\t\t\txm_file=xm-darwin.h\n\t\t\txmake_file=x-darwin\n\t\t\tfixincludes=Makefile.in\n\t\t\t;;\n" + case,
            1,
        )
        configure.chmod(configure.stat().st_mode | 0o200)
        configure.write_text(config_text, encoding="latin-1")
        configure.chmod(0o755)
        shutil.copyfile(patches / "xm-darwin.h", source / "config" / "xm-darwin.h")
        # Older configure scripts otherwise fall back to the MIPS host Makefile and pass IRIX flags to clang.
        (source / "config" / "x-darwin").write_text("# Native Darwin host: use the system clang defaults.\n")
        for name in ("cp/parse.y", "cp/parse.h", "cp/parse.c", "insn-config.h"):
            path = source / name
            if path.exists():
                path.chmod(path.stat().st_mode | 0o200)
                path.touch()

        build = root / "build"
        build.mkdir()
        (build / "insn-config.h").touch()
        cflags = "-std=gnu89 -w -Wno-int-conversion -Wno-implicit-function-declaration -Wno-return-mismatch"
        env["CFLAGS"] = cflags
        host_flags = ["--host=" + _platform.machine() + "-apple-darwin",
                      "--build=" + _platform.machine() + "-apple-darwin"]
        commands = [
            [str(source / "configure"), "--target=mips-sony-psx", "--prefix=/opt/cross",
             "--with-endian-little", "--with-gnu-as", *host_flags],
            ["/usr/bin/make", "-j" + str(min(4, os.cpu_count() or 1)), "cpp", "cc1", "xgcc", "cc1plus", "g++",
             "CFLAGS=" + cflags],
        ]
        for argv in commands:
            log(f"  building native GCC {version} for PlayStation (first install only): " + Path(argv[0]).name)
            try:
                res = subprocess.run(limits.capped(argv), cwd=build, capture_output=True, text=True,
                                     errors="replace", timeout=900, env=dict(env, CC="/usr/bin/clang"))
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ToolchainError(f"Couldn't build GCC {version} for PlayStation: {error}. Install Xcode Command Line Tools and retry.") from error
            if res.returncode:
                raise ToolchainError(f"Couldn't build GCC {version} for PlayStation. Install Xcode Command Line Tools and retry.\n"
                                     + (res.stdout + res.stderr)[-4000:])

        # Check the target's byte order and the GCC extensions expected by PSY-Q before exposing the toolchain.
        smoke = root / "little_endian.c"
        smoke.write_text("unsigned char read_byte(const unsigned char *p) { return *p; }\n", encoding="utf-8")
        asm = root / "little_endian.s"
        try:
            res = subprocess.run([str(build / "cc1"), "-quiet", "-O2", str(smoke), "-o", str(asm)],
                                 capture_output=True, text=True, errors="replace", timeout=30, env=env)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ToolchainError(f"GCC {version} failed its target smoke test: {error}") from error
        if res.returncode or not re.search(r"lbu\s+\$2,0\(\$4\)", asm.read_text(errors="replace")):
            raise ToolchainError(f"GCC {version} failed its little-endian MIPS smoke test.\n" + (res.stdout + res.stderr)[-2000:])
        outputs = {"cpp": "cpp", "cc1": "cc1", "gcc": "xgcc", "cc1plus": "cc1plus", "g++": "g++"}
        for name, binary in outputs.items():
            target = dest / name
            if not (build / binary).is_file():
                raise ToolchainError(f"The pinned PS1 GCC build did not produce {name} ({binary}).")
            shutil.copy2(build / binary, target)
            target.chmod(0o755)


def venv_bin(venv: Path, name: str) -> Path:
    """Path of an executable inside a virtual environment."""
    return venv / "Scripts" / f"{name}.exe" if sys.platform == "win32" else venv / "bin" / name


def _install_python_env(tc: Toolchain, dest: Path, log) -> None:
    """An isolated environment for a Python tool: its pinned git package, or for a folder of scripts unpacked from
    its parts (decomp-permuter isn't a package), only the libraries those scripts import (`pip_extra`)."""
    uv = find_uv()
    if not uv:
        raise ToolchainError(f"{tc.id} needs `uv` to create its isolated environment")
    specs = []
    if tc.git and not shutil.which("git"):
        # uv can fetch the pinned commit as an archive when git is missing
        specs = [f"{tc.id} @ {tc.git.removesuffix('.git')}/archive/{tc.commit}.zip"]
    elif tc.git:
        specs = [f"{tc.id} @ git+{tc.git}@{tc.commit}"]
    log(f"  creating an isolated environment for {f'{tc.git} @ {tc.commit[:10]}' if tc.git else tc.name}")
    subprocess.run([uv, "venv", "--quiet", "--python", "3.12", str(dest / "venv")], check=True, creationflags=NO_WINDOW)
    subprocess.run([uv, "pip", "install", "--quiet", "--python", str(venv_bin(dest / "venv", "python")), *specs,
                    *tc.pip_extra], check=True, creationflags=NO_WINDOW)


def binary(tid: str) -> Path:
    """The main executable of an installed tool (objdiff-cli, dtk, ...)."""
    tc = get(tid)
    base = require(tid)
    name = tc.binary_name or tid
    for candidate in (base / f"{name}.exe", base / name, base / "bin" / f"{name}.exe", base / "bin" / name):
        if candidate.exists():
            return candidate
    raise ToolchainError(f"{tid} is installed but {name} wasn't found in {base}")


_ensure_locks: dict[str, threading.RLock] = {}
_ensure_locks_guard = threading.Lock()

def ensure(tid: str, log=print) -> Path:
    """Install a free tool once across concurrent workers; proprietary packs require explicit acceptance."""
    with _ensure_locks_guard:
        lock = _ensure_locks.setdefault(tid, threading.RLock())
    with lock:
        if installed(tid) is None:
            tc = get(tid)
            if tc.proprietary:
                raise ToolchainError(f"{tc.name} is proprietary: install it from Setup if you accept its license")
            install(tid, log=log)
        return path(tid)


JDK_HINT = ("a Java development kit (JDK), not the Java runtime alone: your system's JDK package (Fedora: "
            "java-25-openjdk-devel; Debian, Ubuntu: openjdk-21-jdk) or one from https://adoptium.net")


def jdk_version() -> int:
    """Major version of the java on PATH when it's a JDK's (javac next to it, as Ghidra's launcher wants), else 0. A
    runtime alone passed the Java check, and Ghidra then quit at once ("Unable to prompt user for JDK path")."""
    exe = shutil.which("java")
    if not exe:
        return 0
    javac = Path(os.path.realpath(exe)).with_name("javac.exe" if os.name == "nt" else "javac")
    return java_version() if javac.exists() else 0


def java_version() -> int:
    """Major version of the java on PATH, 0 if none."""
    import re as _re

    exe = shutil.which("java")
    if not exe:
        return 0
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=20,
                             encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    m = _re.search(r'version "(\d+)', out.stderr + out.stdout)
    return int(m.group(1)) if m else 0


def _local_status(tc: Toolchain, m: dict) -> dict:
    """Where the copies of a kit the user set up are, each with its build and whether its files are ones
    decompdecomp knows (Kit.verify). `folder` and `verified` are the first copy's."""
    from . import kits

    kit, copies = kits.get(tc.id), []
    for c in _copies(m):
        at = find_local(tc, c)
        try:
            copies.append({"folder": c, "build": (kit.build(at) or "") if at else "",
                           "verified": kit.verify(at) if at else "it isn't there any more: remove it or put it back"})
        except OSError:
            copies.append({"folder": c, "build": "", "verified": ""})
    return {"folder": copies[0]["folder"], "verified": copies[0]["verified"], "copies": copies} if copies else {}


def status() -> list[dict]:
    """Every pinned compiler and tool: whether it's installed and can be on this computer, its licence, and whether
    its downloads are pinned. For Compilers & tools."""
    rows = []
    tools = registry()
    dependency_cache = {}
    def missing(tc, linux, vm, seen):
        result = []
        for tid in tc.requires:
            if tid in seen:
                continue
            seen.add(tid)
            cache_key = (tid, linux, vm)
            if cache_key not in dependency_cache:
                dependency_cache[cache_key] = installed(tid, linux, vm=vm)
            if dependency_cache[cache_key] is None:
                result.append({'id':tid, 'name':tools[tid].name})
            else:
                result.extend(missing(tools[tid], linux, vm, seen))
        return result
    for tc in tools.values():
        m = installed(tc.id)
        rows.append({"id": tc.id, "name": tc.name, "kind": tc.kind, "platform": tc.platform, "license": tc.license,
                     "source": tc.source,  # (where it comes from: the install's confirmation says)
                     "installed": m is not None, "supported": tc.supported, "description": tc.description,
                     "missing_dependencies": missing(tc, tc.via_wsl or tc.via_vm, True if tc.via_vm else None, {tc.id}) if m else [],
                     "changed": m is None and _manifest(tc.id) is not None,
                     "wsl": tc.via_wsl, "linux_vm": tc.via_vm, "local": tc.local,
                     **(_local_status(tc, m) if tc.local and m else {}),
                     "pinned": all(p.get("sha256") for p in tc.parts) or bool(tc.commit)})
    return rows
