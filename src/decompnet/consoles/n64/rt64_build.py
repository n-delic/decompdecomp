"""Build the optional macOS RT64 adapter from pinned public sources.

`tools/build_rt64_adapter.py` runs this by hand, and the app runs it in the background when it starts
(`app/rt64_autobuild.py`), so N64ModernRuntime's Play is there without a manual step. Nothing here downloads Apple's
Metal Toolchain or Xcode: `problem()` says what is missing and the build stops there. Sources are fetched at the
revisions pinned in `UPSTREAM`; `stamp()` says which revisions and which adapter code a built library came from, so
the app rebuilds only when one of them changes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ... import limits, toolchains

UPSTREAM = {
    "modern": ("https://github.com/N64Recomp/N64ModernRuntime.git", "e4342ae030a9d94aa97a81aafd3368ed7daa74df"),
    "rt64": ("https://github.com/rt64/rt64.git", "d9817c68ffd7328cd0af383af124971489366e18"),
    "sdl2": ("https://github.com/libsdl-org/SDL.git", "5d249570393f7a37e037abf22cd6012a4cc56a71"),
}
ADAPTER = Path(__file__).resolve().parent / "port_wasm/rt64/adapter.cpp"
LIBRARY = "libdecompnet-rt64.dylib"
STAMP = "build.json"


class BuildError(Exception):
    """The build can't run or failed, with a sentence the user can act on."""


def folder() -> Path:
    return toolchains.root() / "rt64-adapter"


def stamp() -> dict:
    """What a library built now would be made from: the pinned sources and the adapter's own code."""
    return {"sources": {name: revision for name, (_url, revision) in UPSTREAM.items()},
            "adapter": hashlib.sha256(ADAPTER.read_bytes()).hexdigest()}


def built(destination: Path | None = None) -> bool:
    """True when the folder holds a library made from the current stamp."""
    destination = destination or folder()
    try:
        return (destination / LIBRARY).is_file() and json.loads((destination / STAMP).read_text()) == stamp()
    except (OSError, ValueError):
        return False


def _environment() -> dict:
    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    return env


def _metal_environment(env: dict) -> dict:
    component = subprocess.run(["xcodebuild", "-showComponent", "MetalToolchain"], env=env, capture_output=True,
                               text=True, timeout=30)
    selected = re.search(r"^Toolchain Identifier: (.+)$", component.stdout, re.M)
    return {**env, **({"TOOLCHAINS": selected[1]} if selected else {})}


def problem() -> str:
    """Why the adapter can't be built on this computer, or '' when it can. Never installs anything."""
    if sys.platform != "darwin":
        return "The RT64 compatibility adapter currently supports macOS desktop Play."
    env = _environment()
    if not Path(env["DEVELOPER_DIR"]).is_dir():
        return "Install Xcode, then build the RT64 adapter again."
    if not shutil.which("git"):
        return "Install Git, then build the RT64 adapter again."
    if not Path(sys.executable).with_name("ninja").is_file():
        return "Run the build with the environment `uv sync` creates (it holds ninja)."
    try:
        metal = subprocess.run(["xcrun", "metal", "--version"], env=_metal_environment(env), capture_output=True,
                               timeout=60)
    except (OSError, subprocess.SubprocessError):
        return "Xcode's command line tools did not answer."
    if metal.returncode:
        return "Install the Metal Toolchain in Xcode’s Components settings, then build the RT64 adapter again."
    return ""


def run(*args, env, cwd=None):
    subprocess.run(limits.capped([str(arg) for arg in args]), env=env, cwd=cwd, check=True, timeout=1800)


def checkout(work, name, env):
    url, revision = UPSTREAM[name]
    source = work / name
    if not source.exists():
        source.mkdir()
        run("git", "init", "-q", env=env, cwd=source)
        run("git", "remote", "add", "origin", url, env=env, cwd=source)
    run("git", "fetch", "--depth=1", url, revision, env=env, cwd=source)
    run("git", "checkout", "--detach", revision, env=env, cwd=source)
    run("git", "submodule", "update", "--init", "--recursive", "--depth=1", "--jobs=4", env=env, cwd=source)
    return source.resolve()


def notices(output, sources, env):
    licenses = output / "licenses"
    licenses.mkdir(exist_ok=True)
    for name, source in zip(UPSTREAM, sources):
        submodules = subprocess.check_output(["git", "submodule", "status", "--recursive"], cwd=source,
                                             env=env, text=True, timeout=60)
        folders = [source, *(source / line.split()[1] for line in submodules.splitlines())]
        for folder_ in folders:
            for pattern in ("LICENSE*", "COPYING*", "NOTICE*"):
                for file in folder_.glob(pattern):
                    if file.is_file():
                        target = licenses / name / file.relative_to(source)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(file, target)
    (licenses / "sources.txt").write_text(
        "".join(f"{name}: {url} at {revision} (including pinned submodules)\n"
                for name, (url, revision) in UPSTREAM.items()), encoding="utf-8")


def build(output: Path | None = None) -> Path:
    """Build the adapter into `output` (the toolchains folder's `rt64-adapter` by default) and return its library."""
    if reason := problem():
        raise BuildError(reason)
    env = _environment()
    metal_env = _metal_environment(env)
    output = (output or folder()).expanduser().resolve()
    work = output / ".build"
    work.mkdir(parents=True, exist_ok=True)
    toolchains.ensure("cmake")
    cmake = toolchains.binary("cmake")
    ninja = Path(sys.executable).with_name("ninja")
    print("Fetching the pinned sources", flush=True)
    modern, rt64, sdl = (checkout(work, name, env) for name in UPSTREAM)
    sdl_install = (work / "sdl2-install").resolve()
    common = ("-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={ninja}", "-DCMAKE_BUILD_TYPE=Release")
    for name, source, options, targets, build_env in (
        ("sdl2", sdl, (f"-DCMAKE_INSTALL_PREFIX={sdl_install}", "-DSDL_TEST=OFF", "-DSDL_STATIC=OFF"), (), env),
        ("modern", modern, (), ("--target", "ultramodern"), env),
        ("rt64", rt64, (f"-DCMAKE_PREFIX_PATH={sdl_install}", "-DRT64_STATIC=ON"), ("--target", "rt64"), metal_env),
    ):
        print(f"Building {name}", flush=True)
        build_dir = (work / f"{name}-build").resolve()
        run(cmake, "-S", source, "-B", build_dir, *common, *options, env=env)
        run(cmake, "--build", build_dir, "--parallel", os.environ.get("DECOMP_RT64_JOBS", "4"), *targets,
            env=build_env)
        if source == sdl:
            # SDL's install makes libSDL2.dylib with `execute_process(COMMAND <cmake> -E create_symlink ...)`, which
            # splits at the space in macOS's "Application Support" (where the toolchains live) and leaves no link;
            # the install then stops on the missing file. Make the link first.
            link = build_dir / "libSDL2.dylib"
            if not link.exists() and not link.is_symlink():
                link.symlink_to("libSDL2-2.0.dylib")
            run(cmake, "--install", build_dir, env=env)

    print("Linking the adapter", flush=True)
    src = rt64 / "src"
    built_dir = work / "rt64-build"
    contrib = built_dir / "src/contrib"
    includes = (src, src / "contrib", src / "contrib/plume", src / "contrib/hlslpp/include", src / "contrib/imgui",
                src / "contrib/plume/contrib/Vulkan-Headers/include", src / "contrib/plume/contrib/metal-cpp",
                built_dir / "src", sdl_install / "include/SDL2", modern / "ultramodern/include",
                modern / "thirdparty/concurrentqueue")
    libraries = (built_dir / "rt64.a", work / "modern-build/ultramodern/libultramodern.a",
                 contrib / "plume/libplume.a", contrib / "zstd/build/cmake/lib/libzstd.a",
                 contrib / "re-spirv/libre-spirv.a", contrib / "nativefiledialog-extended/src/libnfd.a")
    frameworks = ("Cocoa", "Metal", "QuartzCore", "CoreGraphics", "IOKit")
    library = output / LIBRARY
    pending = output / "libdecompnet-rt64.pending.dylib"
    run("xcrun", "clang++", "-dynamiclib", "-std=c++20", "-O2", "-DHLSL_CPU", "-DFFX_GCC",
        "-DIMGUI_IMPL_VULKAN_NO_PROTOTYPES", *(f"-I{path}" for path in includes), ADAPTER, *libraries,
        f"-L{sdl_install / 'lib'}", "-lSDL2", "-Wl,-rpath,@loader_path",
        *(arg for name in frameworks for arg in ("-framework", name)), "-o", pending, env=env)
    shutil.copyfile(sdl_install / "lib/libSDL2-2.0.0.dylib", output / "libSDL2-2.0.0.dylib")
    notices(output, (modern, rt64, sdl), env)
    pending.replace(library)
    (output / STAMP).write_text(json.dumps(stamp()), encoding="utf-8")
    return library


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, nargs="?", default=None,
                        help="Folder for the adapter, SDL2 and reusable build files")
    args = parser.parse_args(argv)
    try:
        library = build(args.destination)
    except BuildError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"Built {library}. Refresh Porting. For a custom folder, set DECOMPNET_RT64_ADAPTER={library}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
