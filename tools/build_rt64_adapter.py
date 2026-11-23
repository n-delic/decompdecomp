"""Build the optional macOS RT64 adapter from pinned public sources."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from decompnet import limits, toolchains

UPSTREAM = {
    "modern": ("https://github.com/N64Recomp/N64ModernRuntime.git", "e4342ae030a9d94aa97a81aafd3368ed7daa74df"),
    "rt64": ("https://github.com/rt64/rt64.git", "d9817c68ffd7328cd0af383af124971489366e18"),
    "sdl2": ("https://github.com/libsdl-org/SDL.git", "5d249570393f7a37e037abf22cd6012a4cc56a71"),
}
ADAPTER = (Path(__file__).resolve().parents[1]
           / "src/decompnet/consoles/n64/port_wasm/rt64/adapter.cpp")


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
        for folder in folders:
            for pattern in ("LICENSE*", "COPYING*", "NOTICE*"):
                for file in folder.glob(pattern):
                    if file.is_file():
                        target = licenses / name / file.relative_to(source)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(file, target)
    (licenses / "sources.txt").write_text(
        "".join(f"{name}: {url} at {revision} (including pinned submodules)\n"
                for name, (url, revision) in UPSTREAM.items()), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, nargs="?", default=toolchains.root() / "rt64-adapter",
                        help="Folder for the adapter, SDL2 and reusable build files")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("The RT64 compatibility adapter currently supports macOS desktop Play.")
    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    component = subprocess.run(
        ["xcodebuild", "-showComponent", "MetalToolchain"], env=env, capture_output=True, text=True, timeout=30)
    selected = re.search(r"^Toolchain Identifier: (.+)$", component.stdout, re.M)
    metal_env = {**env, **({"TOOLCHAINS": selected[1]} if selected else {})}
    try:
        run("xcrun", "metal", "--version", env=metal_env)
    except subprocess.CalledProcessError:
        parser.error("Install the Metal Toolchain in Xcode’s Components settings, then run the build again.")

    output = args.destination.expanduser().resolve()
    work = output / ".build"
    work.mkdir(parents=True, exist_ok=True)
    toolchains.ensure("cmake")
    cmake = toolchains.binary("cmake")
    ninja = Path(sys.executable).with_name("ninja")
    if not ninja.is_file():
        parser.error("Run this script with the development environment created by uv sync.")
    modern, rt64, sdl = (checkout(work, name, env) for name in UPSTREAM)
    sdl_install = (work / "sdl2-install").resolve()
    common = ("-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={ninja}", "-DCMAKE_BUILD_TYPE=Release")
    for name, source, options, targets, build_env in (
        ("sdl2", sdl, (f"-DCMAKE_INSTALL_PREFIX={sdl_install}", "-DSDL_TEST=OFF", "-DSDL_STATIC=OFF"), (), env),
        ("modern", modern, (), ("--target", "ultramodern"), env),
        ("rt64", rt64, (f"-DCMAKE_PREFIX_PATH={sdl_install}", "-DRT64_STATIC=ON"), ("--target", "rt64"), metal_env),
    ):
        build = (work / f"{name}-build").resolve()
        run(cmake, "-S", source, "-B", build, *common, *options, env=env)
        run(cmake, "--build", build, "--parallel", "4", *targets, env=build_env)
        if source == sdl:
            # SDL's install makes libSDL2.dylib with `execute_process(COMMAND <cmake> -E create_symlink ...)`, which
            # splits at the space in macOS's "Application Support" (where the toolchains live) and leaves no link;
            # the install then stops on the missing file. Make the link first.
            link = build / "libSDL2.dylib"
            if not link.exists() and not link.is_symlink():
                link.symlink_to("libSDL2-2.0.dylib")
            run(cmake, "--install", build, env=env)

    src = rt64 / "src"
    built = work / "rt64-build"
    contrib = built / "src/contrib"
    includes = (src, src / "contrib", src / "contrib/plume", src / "contrib/hlslpp/include", src / "contrib/imgui",
                src / "contrib/plume/contrib/Vulkan-Headers/include", src / "contrib/plume/contrib/metal-cpp",
                built / "src", sdl_install / "include/SDL2", modern / "ultramodern/include",
                modern / "thirdparty/concurrentqueue")
    libraries = (built / "rt64.a", work / "modern-build/ultramodern/libultramodern.a", contrib / "plume/libplume.a",
                 contrib / "zstd/build/cmake/lib/libzstd.a", contrib / "re-spirv/libre-spirv.a",
                 contrib / "nativefiledialog-extended/src/libnfd.a")
    frameworks = ("Cocoa", "Metal", "QuartzCore", "CoreGraphics", "IOKit")
    library = output / "libdecompnet-rt64.dylib"
    pending = output / "libdecompnet-rt64.pending.dylib"
    run("xcrun", "clang++", "-dynamiclib", "-std=c++20", "-O2", "-DHLSL_CPU", "-DFFX_GCC",
        "-DIMGUI_IMPL_VULKAN_NO_PROTOTYPES", *(f"-I{path}" for path in includes), ADAPTER, *libraries,
        f"-L{sdl_install / 'lib'}", "-lSDL2", "-Wl,-rpath,@loader_path",
        *(arg for name in frameworks for arg in ("-framework", name)), "-o", pending, env=env)
    shutil.copyfile(sdl_install / "lib/libSDL2-2.0.0.dylib", output / "libSDL2-2.0.0.dylib")
    notices(output, (modern, rt64, sdl), env)
    pending.replace(library)
    print(f"Built {library}. Refresh Porting. For a custom folder, set DECOMPNET_RT64_ADAPTER={library}.")


if __name__ == "__main__":
    main()
