"""Exports again on GitHub: each kind of export the user makes (export.py) also adds a GitHub Actions workflow to the
project's repository that makes the same export there whenever a release is published, never on pull requests or
pushes: the browser build deployed to the repository's GitHub Pages, the others attached to the release.

A runner has the save repository alone: none of the app's database and no ROM. So the export also writes what the
build took from the project into `port/wasm/export.json` (the library code the host provides, library code's own
names, the functions built from ultralib's C, the game's title and version), and the workflow runs
`python -m decompnet.port.wasm export` on it with the same decompdecomp the user exported with (its commit, or its
release tag). What a runner can't do, it leaves out and says so: the handhelds' packs (the Miyoo Mini's, the Steam
Deck's) have no ROM in them (the player adds theirs), and the apps have no C recompiled from the ROM; a function with
no C yet runs instead from the player's own ROM, interpreted as the game plays (consoles/n64/port_wasm/mips.c), as in
every other build.

Both files are committed to the repository (those paths only: the user's other changes stay as they are), never
pushed: the user's next push takes them.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

MANIFEST = Path("port") / "wasm" / "export.json"
REPOSITORY = "https://github.com/RetroReversing/decompdecomp.git"
# The runner each exported system builds on
RUNNERS = {"darwin": ("macos-latest", "macOS"), "linux": ("ubuntu-latest", "Linux"), "win32": ("windows-latest", "Windows")}


def pin() -> str:
    """The decompdecomp the workflow installs: this copy's commit when it knows it, else its release's tag."""
    from ... import __version__
    from ...app import build_info
    commit = build_info.commit()
    return commit if re.fullmatch(r"[0-9a-f]{40}", commit or "") else f"v{__version__}"


def manifest(net, slug: str, cfg: dict, inputs: dict, simd: bool) -> dict:
    """What a runner's build takes besides the save, from the project as the user's export took it."""
    from ...app.sdk_assembly import ultralib_set_aside
    with net.progress.view():
        proj = net._project(slug)
    rom = cfg.get("rom", {})
    return {"console": cfg.get("platform", "n64"), "title": rom.get("title") or slug,
            "rom": {k: rom.get(k) for k in ("title", "region", "sha1") if rom.get(k)},
            "leave_out": sorted(inputs.get("leave_out") or ()), "library_names": dict(sorted((inputs.get("library_names")
                                                                                          or {}).items())),
            "ultralib": {name: list(found) for name, found in sorted(ultralib_set_aside(net, proj).items())},
            "simd": bool(simd)}


def read_manifest(repo: Path) -> dict:
    from ...network import NetworkError
    try:
        data = json.loads((Path(repo) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise NetworkError(f"{MANIFEST} is missing or unreadable ({error}): export the game once from decompdecomp, "
                           "which writes it") from None
    if not isinstance(data, dict):
        raise NetworkError(f"{MANIFEST} isn't what an export writes: export the game again from decompdecomp")
    return data


def _top(repo: Path) -> tuple[Path, str]:
    """The git repository holding the save (the workflow goes in its .github/), and the save's folder in it."""
    git = shutil.which("git")
    if git:
        done = subprocess.run([git, "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if done.returncode == 0 and done.stdout.strip():
            top = Path(done.stdout.strip()).resolve()
            rel = Path(repo).resolve().relative_to(top).as_posix()
            return top, "." if rel in ("", ".") else rel
    return Path(repo).resolve(), "."


def _quote(text: str) -> str:
    return json.dumps(text)  # a YAML double-quoted string


INSTALL = """\
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@v6
      - name: Export {what} with decompdecomp
        shell: bash
        run: >
          uv run --no-project --python 3.12 --with {package}
          python -m decompnet.port.wasm export {save} --kind {kind} --out "$RUNNER_TEMP/export"{extra}
"""

HEADER = """\
# Made by decompdecomp's Export: {what}, made again on every published release (never on pull requests or
# pushes) from this repository's saved C, with the decompdecomp it was exported with. Exporting again from
# decompdecomp rewrites this file.
name: {name}
on:
  release:
    types: [published]
"""

PAGES = HEADER + """\
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
""" + INSTALL + """\
      - uses: actions/upload-pages-artifact@v3
        with:
          path: ${{{{ runner.temp }}}}/export
  deploy:
    # The repository's Settings > Pages > Source must be "GitHub Actions" (once).
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{{{ steps.deployment.outputs.page_url }}}}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
"""

RELEASE = HEADER + """\
permissions:
  contents: write
jobs:
  export:
    runs-on: {runner}
    steps:
""" + INSTALL + """\
      - name: Attach it to the release
        shell: bash
        env:
          GH_TOKEN: ${{{{ github.token }}}}
          TAG: ${{{{ github.event.release.tag_name }}}}
        run: |
          cd "$RUNNER_TEMP/export"
          {pack}
          gh release upload "$TAG" "$RUNNER_TEMP/{asset}" --clobber --repo "$GITHUB_REPOSITORY"
"""


def _asset(title: str, kind: str, system: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-") or "game"
    return f"{base}-{kind}-{system.lower()}"


def workflow(kind: str, platform: str, title: str, save: str, needs_ultralib: bool) -> tuple[str, str]:
    """The workflow for an export of `kind` made on `platform`: (its file name, its text)."""
    package = _quote(f"decompnet @ git+{REPOSITORY}@{pin()}")
    save = _quote(save)
    extra = " --ultralib" if needs_ultralib else ""
    if kind == "web":
        what = "the browser build to GitHub Pages"
        text = PAGES.format(what=what, name=_quote(f"Export {title} for the web (GitHub Pages)"), package=package, save=save,
                            kind=kind, extra=extra)
        return "export-web.yml", text
    if kind == "ios":
        runner, system = RUNNERS["linux"][0], "ios"
    elif kind in ("miyoo", "steamdeck"):
        runner, system = RUNNERS["linux"][0], kind
    else:
        runner, system = RUNNERS.get(platform, RUNNERS["linux"])
    asset = _asset(title, kind, system)
    if kind in ("miyoo", "steamdeck", "ios") or platform == "linux":
        pack, asset = f'tar -czf "$RUNNER_TEMP/{asset}.tar.gz" .', f"{asset}.tar.gz"
    elif platform == "darwin":  # ditto keeps the app's signature and its links
        pack, asset = f'ditto -c -k --keepParent "$(ls -d *.app | head -1)" "$RUNNER_TEMP/{asset}.zip"', f"{asset}.zip"
    else:
        pack, asset = f'7z a -tzip "$RUNNER_TEMP/{asset}.zip" .', f"{asset}.zip"
    what = {"app": f"the standalone app for {system}", "window": f"the app with its own window for {system}",
            "miyoo": "the Miyoo Mini's SD card folders", "steamdeck": "the Steam Deck's folder", "ios": "the iPhone/iPad Xcode project (unsigned)"}[kind]
    text = RELEASE.format(what=what, name=_quote(f"Export {title}: {what}"), runner=runner, package=package, save=save,
                          kind=kind, extra=extra, pack=pack, asset=asset)
    name = f"export-{kind}.yml" if kind in ("miyoo", "steamdeck", "ios") else f"export-{kind}-{system.lower()}.yml"
    return name, text


def write(repo: Path, kind: str, platform: str, data: dict, log=print) -> dict:
    """The manifest and `kind`'s workflow into the repository holding the save `repo`, committed (those files
    alone) when it's a git repository: {"workflow", "manifest", "committed"}."""
    repo = Path(repo)
    top, save = _top(repo)
    manifest_path = repo / MANIFEST
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    name, text = workflow(kind, platform, data.get("title") or "game", save, bool(data.get("ultralib")))
    path = top / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    committed = False
    git = shutil.which("git")
    if git and (top / ".git").exists():
        paths = [str(p.relative_to(top)) for p in (path, manifest_path.resolve())]
        subprocess.run([git, "-C", str(top), "add", "-f", "--", *paths], capture_output=True, text=True)
        done = subprocess.run([git, "-C", str(top), "commit", "-q", "-m", f"Export {data.get('title') or 'the game'}"
                               f" again on each release ({name})", "--", *paths], capture_output=True, text=True)
        committed = done.returncode == 0
        if not committed and "nothing" not in (done.stdout + done.stderr):
            log(f"the export's workflow is in {path}, but committing it failed: {(done.stderr or done.stdout).strip()[-300:]}")
    log(f"added {path.relative_to(top)}: each published release makes this export again on GitHub"
        + (" (committed; push to use it)" if committed else ""))
    return {"workflow": str(path), "manifest": str(manifest_path), "committed": committed}
