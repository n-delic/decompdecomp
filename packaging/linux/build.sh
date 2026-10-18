#!/usr/bin/env bash
# decompdecomp as a Linux app: a .deb, an .rpm and an AppImage (x86_64) in dist/, built by the release workflow on
# each tag.
#
# All three carry their own Python 3.12 (python-build-standalone, which uv installs) with decompdecomp in it, and uv
# itself (git-hosted tools like splat get an environment of their own from it). The window comes from the system:
# GTK and WebKitGTK 4.1 with their Python bindings, which the .deb and the .rpm depend on, and the AppImage asks for
# when they're missing (app/window.py). Compilers and tools come later, from Setup, as with every install.
# Run from anywhere: packaging/linux/build.sh (the .rpm needs rpmbuild; without it, it's skipped)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
HERE="$ROOT/packaging/linux"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.0/appimagetool-x86_64.AppImage"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
OUT="$ROOT/dist"
mkdir -p "$OUT"

# 1. decompdecomp, installed into a Python of its own
(cd "$ROOT" && uv build --wheel --out-dir "$WORK/wheel" >/dev/null)
uv python install 3.12 >/dev/null
PY=$(uv python find --managed-python 3.12)
APP="$WORK/opt/decompdecomp"
mkdir -p "$APP/bin"
cp -a "$(dirname "$(dirname "$(readlink -f "$PY")")")" "$APP/python"
rm -rf "$APP/python/lib/python3.12/EXTERNALLY-MANAGED" "$APP/python/lib/python3.12/test" "$APP/python/lib/python3.12/idlelib"
uv pip install --quiet --python "$APP/python/bin/python3.12" --break-system-packages "$WORK"/wheel/*.whl
rm -f "$APP/python/bin/decomp"  # its shebang names this build's folder: the launchers below run the module instead
cp "$(command -v uv)" "$APP/bin/uv"
find "$APP/python" -name __pycache__ -prune -exec rm -rf {} +

# The launchers set DECOMP_PACKAGE: the updater then sends people to the releases page instead of installing over
# the package with uv.

# 2. the .deb: /opt/decompdecomp, the decomp command, the apps-menu entry
DEB="$WORK/deb"
mkdir -p "$DEB/DEBIAN" "$DEB/opt" "$DEB/usr/bin" "$DEB/usr/share/applications" "$DEB/usr/share/icons/hicolor/scalable/apps"
cp -a "$APP" "$DEB/opt/"
cat > "$DEB/usr/bin/decomp" <<'LAUNCHER'
#!/bin/sh
export DECOMP_PACKAGE=deb PATH="/opt/decompdecomp/bin:$PATH"
exec /opt/decompdecomp/python/bin/python3.12 -m decompnet.app.cli "$@"
LAUNCHER
chmod 755 "$DEB/usr/bin/decomp"
cp "$HERE/decompdecomp.desktop" "$DEB/usr/share/applications/"
cp "$ROOT/src/decompnet/app/static/icon.svg" "$DEB/usr/share/icons/hicolor/scalable/apps/decompdecomp.svg"
cat > "$DEB/DEBIAN/control" <<CONTROL
Package: decompdecomp
Version: ${VERSION/-/\~}
Architecture: amd64
Maintainer: decompdecomp <https://github.com/n-delic/decompdecomp>
Installed-Size: $(du -sk "$DEB" | cut -f1)
Depends: libc6 (>= 2.31), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1
Recommends: git
Section: devel
Priority: optional
Homepage: https://github.com/n-delic/decompdecomp
Description: AI agents decompile the games you own, byte for byte
 decompdecomp lets AI agents turn a game you own into C and C++ that the game's
 own compiler builds to the exact same bytes.
CONTROL
dpkg-deb --root-owner-group --build "$DEB" "$OUT/decompdecomp_${VERSION}_amd64.deb" >/dev/null

# 3. the .rpm (Fedora and the like): the same files; rpm only lists the window's packages as what it needs, since
# everything else is inside (AutoReqProv off: rpm would otherwise ask for every library the bundled Python links)
if command -v rpmbuild >/dev/null; then
  RPM="$WORK/rpm"
  mkdir -p "$RPM/root"
  cp -a "$DEB/opt" "$DEB/usr" "$RPM/root/"
  sed -i 's/DECOMP_PACKAGE=deb/DECOMP_PACKAGE=rpm/' "$RPM/root/usr/bin/decomp"
  cat > "$RPM/decompdecomp.spec" <<SPEC
Name: decompdecomp
Version: ${VERSION/-/\~}
Release: 1
Summary: AI agents decompile the games you own, byte for byte
License: AGPL-3.0-only
URL: https://github.com/n-delic/decompdecomp
BuildArch: x86_64
AutoReqProv: no
Requires: python3-gobject, gtk3, webkit2gtk4.1
Recommends: git
%define debug_package %{nil}
%global __os_install_post %{nil}
%global _build_id_links none
%description
decompdecomp lets AI agents turn a game you own into C and C++ that the game's own compiler builds to the exact
same bytes.
%install
cp -a $RPM/root/. %{buildroot}/
%files
/opt/decompdecomp
/usr/bin/decomp
/usr/share/applications/decompdecomp.desktop
/usr/share/icons/hicolor/scalable/apps/decompdecomp.svg
SPEC
  rpmbuild --quiet -bb --define "_topdir $RPM/top" --define "_rpmdir $OUT" --define "_build_name_fmt %%{NAME}-%%{VERSION}-%%{RELEASE}.%%{ARCH}.rpm" "$RPM/decompdecomp.spec"
else
  echo "rpmbuild isn't installed: no .rpm this time" >&2
fi

# 4. the AppImage: the same app, started by AppRun from wherever the image is mounted
DIR="$WORK/AppDir"
mkdir -p "$DIR/opt"
cp -a "$APP" "$DIR/opt/"
cat > "$DIR/AppRun" <<'LAUNCHER'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")/opt/decompdecomp"
export DECOMP_PACKAGE=appimage PATH="$HERE/bin:$PATH"
exec "$HERE/python/bin/python3.12" -m decompnet.app.cli "$@"
LAUNCHER
chmod 755 "$DIR/AppRun"
cp "$HERE/decompdecomp.desktop" "$DIR/"
cp "$ROOT/src/decompnet/app/static/icon.svg" "$DIR/decompdecomp.svg"
TOOL="$WORK/appimagetool"
curl -fsSL -o "$TOOL" "$APPIMAGETOOL_URL"
chmod +x "$TOOL"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$DIR" "$OUT/decompdecomp-${VERSION}-x86_64.AppImage"

ls -l "$OUT"
