"""iPhone/iPad exports carry the playable browser build, an unsigned Xcode project and device setup steps."""

from __future__ import annotations

import plistlib
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from decompnet.port.wasm import export, ios, play, web, workflows


def browser_build(folder):
    folder.mkdir()
    for name in ("index.html", "play.js", "gl.js", "runtime.js", "touch.js", "settings.js", "touch.css", "build.json",
                 "game-web.wasm", "host-web.wasm", "app-asyncify.wasm", "rom.z64", "game.eep"):
        (folder / name).write_text(name)
    return folder


def test_ios_project_bundles_only_web_files_and_replaces_the_previous_export(tmp_path):
    browser = browser_build(tmp_path / "browser")
    dest = tmp_path / "export"
    project = ios.package(browser, dest, 'A <Game> & "Friends"')
    assert project == dest / "Game.xcodeproj"
    info = plistlib.loads((dest / "Game" / "Info.plist").read_bytes())
    assert info["CFBundleDisplayName"] == 'A <Game> & "Friends"'
    assert info["NSAppTransportSecurity"] == {"NSAllowsLocalNetworking": True}
    files = {p.name for p in (dest / "Game" / "Web").iterdir()}
    assert files == {"index.html", "play.js", "gl.js", "runtime.js", "touch.js", "settings.js", "touch.css", "build.json",
                     "game-web.wasm", "host-web.wasm", "app-asyncify.wasm"}
    assert (project / "xcshareddata" / "xcschemes" / "Game.xcscheme").is_file()
    text = (project / "project.pbxproj").read_text()
    assert 'TARGETED_DEVICE_FAMILY = "1,2"' in text
    assert "Game/Web" in text and "IPHONEOS_DEPLOYMENT_TARGET = 16.4" in text
    assert "DEVELOPMENT_TEAM" not in text
    readme = (dest / "README.txt").read_text()
    assert "Signing & Capabilities" in readme and "Developer Mode" in readme and "No ROM is included" in readme
    (dest / "old.ipa").write_text("old")
    ios.package(browser, dest, "New Game")
    assert not (dest / "old.ipa").exists()
    assert "com.decompdecomp.port.new-game" in (dest / "Game.xcconfig").read_text()


def test_ios_build_uses_the_shared_browser_pipeline_without_rom_recompilation(tmp_path, monkeypatch):
    browser = browser_build(tmp_path / "browser")
    seen = {}

    def build(repo, **options):
        seen.update(options)
        return browser

    monkeypatch.setattr(web, "build", build)
    inputs = {"recomp": tmp_path / "private-recomp", "leave_out": {"sdk.c"}, "library_c": {}, "library_names": {}}
    dest = tmp_path / "ios"
    folder, path = export.make(tmp_path, "ios", dest, console="demo", title="Demo", rom={},
                               rom_file=tmp_path / "private.z64", inputs=inputs, log=lambda *_: None)
    assert folder == dest and path == dest / "Game.xcodeproj"
    assert seen["console"] == "demo" and seen["leave_out"] == {"sdk.c"}
    assert "recomp" not in seen and seen["simd"] is False
    assert "ios" in export.KINDS


def test_project_export_returns_ios_setup_steps_and_the_release_workflow(tmp_path, monkeypatch):
    browser = browser_build(tmp_path / "browser")
    monkeypatch.setattr(play, "game", lambda *_: (tmp_path, {"platform": "demo", "rom": {"title": "Demo"}}, None))
    monkeypatch.setattr(play, "inputs", lambda *_: {})
    monkeypatch.setattr(web, "build", lambda *_, **__: browser)
    monkeypatch.setattr(workflows, "manifest", lambda *_: {"title": "Demo", "console": "demo"})
    result = export.export(None, "demo", "ios", log=lambda *_: None)
    assert result["kind"] == result["system"] == "ios"
    assert result["path"].endswith("/ios/Game.xcodeproj")
    assert "Game/Web/touch.js" in result["files"] and "Game/Web/rom.z64" not in result["files"]
    assert result["bytes"] > 0
    assert any("signing" in step.lower() for step in result["instructions"])
    assert result["github"]["workflow"].endswith("export-ios.yml")
    assert (Path(result["folder"]).parent / ".gitignore").read_text() == "*\n"


def test_touch_controls_hold_independent_fingers_and_clear_cancelled_gestures():
    node = shutil.which("node")
    if node is None:
        pytest.skip("needs node")
    subprocess.run([node, str(Path(__file__).with_name("web_touch.mjs"))], check=True,
                   capture_output=True, text=True, timeout=60)


def test_xcode_project_is_a_valid_property_list(tmp_path):
    plutil = shutil.which("plutil")
    if plutil is None:
        pytest.skip("needs Apple's plutil")
    browser = browser_build(tmp_path / "browser")
    project = ios.package(browser, tmp_path / "ios", "Demo")
    subprocess.run([plutil, "-lint", str(project / "project.pbxproj")], check=True,
                   capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(sys.platform != "darwin", reason="needs Apple's Swift compiler and Network framework")
def test_bundled_file_server_serves_wasm_and_cannot_read_outside_its_folder(tmp_path):
    swift = shutil.which("swiftc")
    if swift is None:
        pytest.skip("needs Apple's Swift compiler")
    from decompnet import limits

    root = tmp_path / "Web"
    root.mkdir()
    (root / "index.html").write_text("demo page")
    (root / "app.wasm").write_bytes(b"\0asm")
    (tmp_path / "secret.txt").write_text("outside the bundle")
    (root / "link.txt").symlink_to(tmp_path / "secret.txt")
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    main = tmp_path / "main.swift"
    main.write_text('''import Foundation
import Darwin
let server = LocalServer(root: URL(fileURLWithPath: CommandLine.arguments[1]),
                         port: UInt16(CommandLine.arguments[2])!)
server.start { error in
    if let error = error { print("ERROR: \\(error)"); fflush(stdout); exit(1) }
    print("READY"); fflush(stdout)
}
RunLoop.main.run()
''')
    executable = tmp_path / "server"
    subprocess.run(limits.capped([swift, "-module-cache-path", str(tmp_path / "cache"),
                                 str(ios.TEMPLATE / "LocalServer.swift"), str(main), "-o", str(executable)]),
                   check=True, capture_output=True, text=True, timeout=120)
    proc = subprocess.Popen([str(executable), str(root), str(port)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "READY"
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(base + "/", timeout=5) as page:
            assert page.read() == b"demo page" and page.headers.get_content_type() == "text/html"
        with urllib.request.urlopen(base + "/app.wasm?version=1", timeout=5) as wasm:
            assert wasm.read() == b"\0asm" and wasm.headers.get_content_type() == "application/wasm"
        for path in ("/%2e%2e/secret.txt", "/link.txt", "/missing"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + path, timeout=5)
            assert error.value.code == 404
    finally:
        proc.terminate()
        proc.wait(timeout=10)
