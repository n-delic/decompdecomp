#!/usr/bin/env bash
# Build a relocatable macOS application using uv's managed Python and the project wheel.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/dist"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

uv python install 3.12 >/dev/null
PYTHON="$(uv python find --system --managed-python 3.12)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")"
ARCH="$(uname -m)"
case "$ARCH" in arm64|x86_64) ;; *) echo "Unsupported Mac architecture: $ARCH" >&2; exit 1 ;; esac

uv build --wheel --out-dir "$WORK/wheel" "$ROOT" >/dev/null
APP="$WORK/decompdecomp.app"
CONTENTS="$APP/Contents"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources" "$OUT"
# Copy the entire managed interpreter so its standard library and shared libraries stay together.
ditto "$(dirname "$(dirname "$PYTHON")")" "$CONTENTS/Runtime"
"$CONTENTS/Runtime/bin/python3.12" -c 'import sys; assert sys.prefix.endswith("Runtime") and sys.prefix == sys.base_prefix'
uv pip install --break-system-packages --python "$CONTENTS/Runtime/bin/python3.12" "$WORK"/wheel/*.whl >/dev/null

"$CONTENTS/Runtime/bin/python3.12" "$ROOT/packaging/stamp_build.py" "$ROOT"

cat > "$CONTENTS/MacOS/decompdecomp" <<'LAUNCH'
#!/bin/sh
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export DECOMP_PACKAGE=macos
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
exec "$HERE/../Runtime/bin/python3.12" -m decompnet.app.cli "$@"
LAUNCH
chmod 755 "$CONTENTS/MacOS/decompdecomp"
cp "$ROOT/src/decompnet/app/static/icon.icns" "$CONTENTS/Resources/decompdecomp.icns"
cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>decompdecomp</string>
<key>CFBundleDisplayName</key><string>decompdecomp</string>
<key>CFBundleIdentifier</key><string>com.retroreversing.decompdecomp</string>
<key>CFBundleVersion</key><string>$VERSION</string>
<key>CFBundleShortVersionString</key><string>$VERSION</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleExecutable</key><string>decompdecomp</string>
<key>CFBundleIconFile</key><string>decompdecomp.icns</string>
<key>LSMinimumSystemVersion</key><string>12.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF

# Test the path that Finder invokes before archiving. It must work after moving the app.
"$CONTENTS/MacOS/decompdecomp" --help >/dev/null
"$CONTENTS/Runtime/bin/python3.12" -c 'import Cocoa, WebKit, decompnet.app.server'
# Keep the app for direct sharing. Archives remain available for release builds.
rm -rf "$OUT/decompdecomp.app"
ditto "$APP" "$OUT/decompdecomp.app"
echo "Built $OUT/decompdecomp.app"
if [ "${1:-}" != "--app-only" ]; then
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUT/decompdecomp_${VERSION}_macos_${ARCH}.zip"
  echo "Built $OUT/decompdecomp_${VERSION}_macos_${ARCH}.zip"
fi
