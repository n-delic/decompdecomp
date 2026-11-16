"""The browser port inside an offline iPhone/iPad app, exported as a self-contained Xcode project.

Xcode and device signing belong to the player: exporting needs only the existing pinned browser tools, on any
desktop. The app serves its bundled files on loopback so WebKit's modules, ROM storage and saves have an HTTP
origin, without private file-access preferences or a server on the player's computer.
"""

from __future__ import annotations

import plistlib
import shutil
from pathlib import Path

TEMPLATE = Path(__file__).with_suffix("")


def instructions(folder: Path) -> list[str]:
    """Steps shown beside the export as well as in its README, including the device-signing requirement."""
    return [f"On a Mac with Xcode, open {folder / 'Game.xcodeproj'}.",
            "Select the Game target → Signing & Capabilities, choose your Apple account's team, and give the app "
            "a unique Bundle Identifier.",
            "Connect your iPhone or iPad, enable Developer Mode if asked, select it as the run destination, "
            "then press Run in Xcode. A simulator can build without signing.",
            "In the app, choose your own ROM from Files. Use the touch controls or a connected gamepad; tap "
            "Sound on for audio. The ROM and saves stay on this device."]


def package(browser: Path, dest: Path, title: str) -> Path:
    """Wrap the finished browser build in an iOS project, without a ROM or a signing identity."""
    from . import export, web

    if dest.exists():
        shutil.rmtree(dest)
    app = dest / "Game"
    app.mkdir(parents=True)
    web.export(browser, app / "Web")
    for name in ("App.swift", "LocalServer.swift"):
        shutil.copyfile(TEMPLATE / name, app / name)
    project = dest / "Game.xcodeproj"
    project.mkdir()
    shutil.copyfile(TEMPLATE / "project.pbxproj", project / "project.pbxproj")
    shared = project / "xcshareddata" / "xcschemes"
    shared.mkdir(parents=True)
    shutil.copyfile(TEMPLATE / "Game.xcscheme", shared / "Game.xcscheme")
    info = {"CFBundleDisplayName": title, "CFBundleName": "$(PRODUCT_NAME)",
            "CFBundleExecutable": "$(EXECUTABLE_NAME)", "CFBundleIdentifier": "$(PRODUCT_BUNDLE_IDENTIFIER)",
            "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1",
            "LSRequiresIPhoneOS": True, "UILaunchScreen": {},
            "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationLandscapeLeft",
                                                 "UIInterfaceOrientationLandscapeRight"],
            "UISupportedInterfaceOrientations~ipad": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationPortraitUpsideDown",
                                                      "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True}}
    (app / "Info.plist").write_bytes(plistlib.dumps(info))
    # The identifier is a build setting, never interpolated into Swift or the project's quoted strings.
    (dest / "Game.xcconfig").write_text(f"PRODUCT_BUNDLE_IDENTIFIER = {export._ident(title)}\n", encoding="utf-8")
    (dest / ".gitignore").write_text("build/\nDerivedData/\nxcuserdata/\n*.ipa\n*.xcarchive\n", encoding="utf-8")
    steps = "\n".join(f"{i}. {step}" for i, step in enumerate(instructions(dest), 1))
    (dest / "README.txt").write_text(
        f"{title} for iPhone and iPad\n\n{steps}\n\n"
        "Requires iOS/iPadOS 16.4 or later. This project bundles the WebAssembly browser port in WKWebView; "
        "it works offline after installation. No ROM is included. Game compatibility and speed depend on the "
        "existing port and the device; exporting does not complete unfinished game code or hardware support.\n\n"
        "A personal Apple account can run it on your own device through Xcode; provisioning may need renewal. "
        "Sharing through TestFlight or the App Store requires your own Apple distribution setup. This export "
        "does not produce a signed IPA or submit anything to Apple.\n\n"
        "Command-line build without device signing (on a Mac with the iOS SDK):\n"
        "  xcodebuild -project Game.xcodeproj -scheme Game -sdk iphoneos "
        "-configuration Release -derivedDataPath build CODE_SIGNING_ALLOWED=NO build\n\n"
        "Apple's device setup: https://developer.apple.com/documentation/xcode/running-your-app-in-simulator-or-on-a-device\n",
        encoding="utf-8")
    return project
