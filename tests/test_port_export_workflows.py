"""Exports again on GitHub (port/wasm/workflows.py): each export adds a workflow to the project's repository that
makes the same export on every published release, never on pull requests or pushes, from the save alone with the
manifest the export writes (port/wasm/export.json)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
import yaml

from decompnet.network import NetworkError
from decompnet.port.wasm import export, workflows

KINDS = [("web", "darwin"), ("miyoo", "darwin"), ("steamdeck", "darwin"), ("app", "darwin"), ("app", "linux"), ("app", "win32"),
         ("window", "darwin"), ("ios", "darwin"), ("ios", "linux"), ("ios", "win32")]


@pytest.mark.parametrize("kind, platform", KINDS)
def test_each_export_runs_on_published_releases_only(kind, platform):
    """Every kind's workflow is valid YAML that runs on a published release and nothing else: not on pull requests
    (which anyone can open) and not on pushes."""
    name, text = workflows.workflow(kind, platform, 'Rally: "64"', ".", False)
    data = yaml.safe_load(text)
    assert data[True] == {"release": {"types": ["published"]}}  # (YAML reads the key `on` as true)
    assert "pull_request" not in text and "push:" not in text
    assert data["name"].startswith('Export Rally: "64"')
    steps = [s for job in data["jobs"].values() for s in job.get("steps", [])]
    run = next(s["run"] for s in steps if "decompnet.port.wasm export" in s.get("run", ""))
    assert f"--kind {kind}" in run and "--ultralib" not in run
    assert f"git+{workflows.REPOSITORY}@{workflows.pin()}" in run  # the decompdecomp it was exported with
    assert name.startswith(f"export-{kind}")


def test_the_browser_build_deploys_to_github_pages():
    _, text = workflows.workflow("web", "darwin", "Rally", ".", False)
    data = yaml.safe_load(text)
    assert data["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
    assert [s.get("uses") for s in data["jobs"]["deploy"]["steps"]] == ["actions/deploy-pages@v4"]
    assert any(s.get("uses") == "actions/upload-pages-artifact@v3" for s in data["jobs"]["build"]["steps"])


@pytest.mark.parametrize("kind, platform, runner, asset", [
    ("miyoo", "darwin", "ubuntu-latest", "Rally-64-miyoo-miyoo.tar.gz"),
    ("steamdeck", "win32", "ubuntu-latest", "Rally-64-steamdeck-steamdeck.tar.gz"),
    ("ios", "darwin", "ubuntu-latest", "Rally-64-ios-ios.tar.gz"),
    ("app", "darwin", "macos-latest", "Rally-64-app-macos.zip"),
    ("app", "linux", "ubuntu-latest", "Rally-64-app-linux.tar.gz"),
    ("app", "win32", "windows-latest", "Rally-64-app-windows.zip")])
def test_the_others_are_attached_to_the_release(kind, platform, runner, asset):
    """The apps build on the system they were exported for, the handhelds' packs on Linux; each is attached to the
    release that started it."""
    _, text = workflows.workflow(kind, platform, "Rally 64", ".", False)
    data = yaml.safe_load(text)
    assert data["permissions"] == {"contents": "write"}
    (job,) = data["jobs"].values()
    assert job["runs-on"] == runner
    assert f'gh release upload "$TAG" "$RUNNER_TEMP/{asset}"' in job["steps"][-1]["run"]


def test_a_save_that_builds_library_code_from_ultralib_says_so():
    _, text = workflows.workflow("web", "linux", "Rally", "saves/rally", True)
    assert '--ultralib' in text and 'export "saves/rally" --kind web' in text


@pytest.mark.skipif(shutil.which("git") is None, reason="git isn't installed")
def test_the_workflow_and_manifest_are_committed_alone(tmp_path):
    """The save in a folder of the project's repository: the workflow goes in the repository's .github/workflows,
    names the save's folder, and the commit holds the workflow and the manifest only (the user's own staged change
    stays staged)."""
    top = tmp_path / "repo"
    save = top / "saves" / "rally"
    (save / "progress").mkdir(parents=True)
    git = ["git", "-C", str(top), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", str(top)], check=True)
    (top / "notes.txt").write_text("mine")
    subprocess.run([*git, "add", "notes.txt"], check=True)
    data = {"title": "Rally 64", "leave_out": ["pfs.c"], "library_names": {}, "ultralib": {}, "simd": False}
    env_git = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.com"}
    import os
    old = {k: os.environ.get(k) for k in env_git}
    os.environ.update(env_git)
    try:
        result = workflows.write(save, "web", "darwin", data, log=lambda *_: None)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert result["committed"]
    assert result["workflow"] == str((top / ".github" / "workflows" / "export-web.yml").resolve())
    assert 'export "saves/rally" --kind web' in (top / ".github" / "workflows" / "export-web.yml").read_text()
    assert json.loads((save / "port" / "wasm" / "export.json").read_text())["leave_out"] == ["pfs.c"]
    files = subprocess.run([*git, "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True).stdout.split()
    assert sorted(files) == [".github/workflows/export-web.yml", "saves/rally/port/wasm/export.json"]
    assert subprocess.run([*git, "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout.split() == ["notes.txt"]


@pytest.mark.parametrize("kind", ["web", "ios"])
def test_an_export_from_the_save_takes_what_the_manifest_says(tmp_path, monkeypatch, kind):
    """The runner's export: the build's inputs from port/wasm/export.json, no ROM and no recompiled C."""
    save = tmp_path / "save"
    (save / "port" / "wasm").mkdir(parents=True)
    (save / "port" / "wasm" / "export.json").write_text(json.dumps({
        "console": "n64", "title": "Rally 64", "rom": {"title": "RALLY", "region": "Europe"},
        "leave_out": ["pfs.c"], "library_names": {"func_80001000": "bzero"}, "ultralib": {}, "simd": True}))
    seen = {}

    def make(repo, kind, dest, **options):
        seen.update(options, kind=kind, repo=repo)
        dest.mkdir(parents=True)
        return dest, dest / "index.html"
    monkeypatch.setattr(export, "make", make)
    export.export_from_save(save, kind, tmp_path / "out", log=lambda *_: None)
    assert seen["inputs"] == {"leave_out": {"pfs.c"}, "library_c": {}, "library_names": {"func_80001000": "bzero"},
                              "recomp": None}
    assert seen["rom_file"] is None and seen["simd"] is True and seen["title"] == "Rally 64"


def test_an_export_from_the_save_needs_the_manifest_and_says_where_ultralib_is_needed(tmp_path, monkeypatch):
    with pytest.raises(NetworkError, match="export.json"):
        export.export_from_save(tmp_path, "web", tmp_path / "out")
    (tmp_path / "port" / "wasm").mkdir(parents=True)
    (tmp_path / "port" / "wasm" / "export.json").write_text(json.dumps({"ultralib": {"guRotateF": ["src/gu/rotate.c",
                                                                                                    "2.0I/libgultra_rom"]}}))
    from decompnet.port import ultralib
    monkeypatch.setattr(ultralib, "root", lambda: None)
    with pytest.raises(NetworkError, match="--ultralib"):
        export.export_from_save(tmp_path, "web", tmp_path / "out")
