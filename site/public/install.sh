#!/usr/bin/env bash
# decompdecomp installer for Linux. Windows and macOS aren't supported in this version.
#
#   curl -fsSL https://decomp.ndelic.com/install.sh | bash
#
# decompdecomp installs on this computer, with uv:
#   1. installs uv, Astral's Python tool manager, if you don't have it (asks first)
#   2. installs decompdecomp (open source, AGPL-3.0) with uv, in its own environment
#   3. checks for what its window needs from your system (GTK and WebKitGTK 4.1), and offers to install it
#   4. downloads the free compilers and the m2c draft decompiler (pinned and checksum-verified)
#   5. adds decompdecomp to your apps menu (replacing an older .deb or .rpm of it) and opens it
#
# Your projects go to ~/Decomp. Nothing is sent anywhere.
# Uninstall: uv tool uninstall decompnet
#
# Options:
#   --no-start          install only
#   --source <what>     where decompdecomp comes from: "release" (default), "branch:<name>" (a branch on GitHub, for
#                       testing; its updates follow that branch) or a folder with decompdecomp's source. Run from a
#                       git checkout, the checkout is used (and updates follow its branch; main's are releases).
# The same through the environment: DECOMP_SOURCE=..., DECOMP_BRANCH=<name>.
set -euo pipefail

VERSION="1.0.4-alpha"
REPO="n-delic/decompdecomp"
START=1
SOURCE="${DECOMP_SOURCE:-}"
[ -z "$SOURCE" ] && [ -n "${DECOMP_BRANCH:-}" ] && SOURCE="branch:${DECOMP_BRANCH}"
while [ $# -gt 0 ]; do
  case "$1" in
    --no-start) START=0 ;;
    --source) shift; SOURCE="${1:-}" ;;
    --source=*) SOURCE="${1#--source=}" ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

has_tty() { (exec </dev/tty) 2>/dev/null; }  # a terminal we can read, even when piped from curl
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
step() { printf '\033[32m▸\033[0m %s\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
ask() {
  local reply
  has_tty || return 0
  printf '%s [Y/n] ' "$1" > /dev/tty
  read -r reply < /dev/tty || true
  [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

# Run from a checkout (site/public/install.sh in the repository): install that checkout
SCRIPT_DIR=""
CHECKOUT=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -f "$SCRIPT_DIR/../../pyproject.toml" ] && grep -q '^name = "decompnet"' "$SCRIPT_DIR/../../pyproject.toml"; then
    CHECKOUT="$(cd "$SCRIPT_DIR/../.." && pwd)"
  fi
fi
[ -n "$SOURCE" ] || SOURCE="${CHECKOUT:-release}"

bold "decompdecomp setup"
OS="$(uname -s)"
case "$OS" in
  Linux) ;;
  *) fail "decompdecomp runs on Linux, on x86-64 PCs (the games' compilers are x86 programs). Windows and macOS aren't supported in this version; a Linux virtual machine works." ;;
esac
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) fail "decompdecomp needs a 64-bit Intel or AMD PC for now: the games' compilers are x86 programs, and this computer's processor is $(uname -m)" ;;
esac
command -v curl >/dev/null || fail "curl is required"

# ------------------------------------------------------------------------------------------- where it comes from
# CHANNEL/BRANCH/COMMIT are remembered (decomp install-info): the app's updates come from the same place.
CHANNEL=release BRANCH="" COMMIT="" SPEC="" SRC_DIR=""
case "$SOURCE" in
  release)
    SPEC="decompnet @ ${DECOMP_WHEEL:-https://github.com/$REPO/archive/refs/tags/v${VERSION}.tar.gz}" ;;
  branch:*)
    CHANNEL=branch BRANCH="${SOURCE#branch:}"
    [ -n "$BRANCH" ] || fail "--source branch:<name> needs a branch name" ;;
  *)
    SRC_DIR="$(cd "$SOURCE" 2>/dev/null && pwd)" || fail "no folder $SOURCE (--source is release, branch:<name> or a folder)"
    [ -f "$SRC_DIR/pyproject.toml" ] && [ -d "$SRC_DIR/src/decompnet" ] || fail "$SRC_DIR isn't decompdecomp's source"
    if command -v git >/dev/null; then  # a checkout: updates follow its branch on GitHub
      BRANCH="$(git -c safe.directory='*' -C "$SRC_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
      COMMIT="$(git -c safe.directory='*' -C "$SRC_DIR" rev-parse HEAD 2>/dev/null || true)"
      [ "$BRANCH" = HEAD ] && BRANCH=""
      # main is the release line: a checkout of it updates on releases, not on every commit
      [ -n "$BRANCH" ] && [ "$BRANCH" != main ] && CHANNEL=branch
    fi ;;
esac

resolve_branch() {  # the branch's newest commit, installed from GitHub's archive of it
  local api
  api="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPO/commits/$BRANCH" 2>/dev/null || true)"
  COMMIT="$(printf '%s' "$api" | grep -m1 '"sha"' | sed 's/.*"sha": *"\([0-9a-f]*\)".*/\1/' || true)"
  if [ -n "$COMMIT" ]; then
    SPEC="decompnet @ https://github.com/$REPO/archive/$COMMIT.tar.gz"
  else  # GitHub's API said no (rate limit): the branch as it is now
    SPEC="decompnet @ https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz"
  fi
}

# ------------------------------------------------------------------------------------------- on this Linux
for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do [ -x "$d/uv" ] && export PATH="$d:$PATH"; done
if ! command -v uv >/dev/null; then
  ask "decompdecomp is installed with uv (Astral's Python tool manager). Install uv now?" || fail "uv is needed: https://docs.astral.sh/uv/"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || fail "uv installed, but it isn't on PATH yet; open a new terminal and run this again"
fi
step "uv $(uv --version | cut -d' ' -f2)"

if [ -n "$SRC_DIR" ]; then
  # a copy of the source (not the checkout itself: building leaves nothing behind in it, and it may be read-only)
  TMP_SRC="$(mktemp -d)"
  cp -R "$SRC_DIR/pyproject.toml" "$SRC_DIR/README.md" "$SRC_DIR/LICENSE" "$SRC_DIR/LICENSE-decomp.wtf" "$SRC_DIR/src" "$TMP_SRC/"
  find "$TMP_SRC" -name __pycache__ -type d -prune -exec rm -rf {} +
  SPEC="$TMP_SRC"
  step "installing decompdecomp from $SRC_DIR${BRANCH:+ ($BRANCH @ ${COMMIT:0:7})}"
elif [ "$CHANNEL" = branch ]; then
  resolve_branch
  step "installing decompdecomp from the $BRANCH branch${COMMIT:+ @ ${COMMIT:0:7}}"
else
  step "installing decompdecomp ${VERSION}"
fi
uv tool install --force --quiet --python 3.12 "$SPEC"
[ -n "${TMP_SRC:-}" ] && rm -rf "$TMP_SRC"
BIN="$(uv tool dir --bin)"
uv tool update-shell >/dev/null 2>&1 || true
"$BIN/decomp" install-info --channel "$CHANNEL" --branch "$BRANCH" --commit "$COMMIT" --repo "$REPO" >/dev/null

# the window: GTK and WebKitGTK 4.1 with their Python bindings, from the system (no browser involved)
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1")' 2>/dev/null; then
  . /etc/os-release 2>/dev/null || true
  case " ${ID:-} ${ID_LIKE:-} " in
    *" debian "*|*" ubuntu "*) WINDOW_PACKAGES="sudo apt install -y python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1" ;;
    *" fedora "*|*" rhel "*) WINDOW_PACKAGES="sudo dnf install -y python3-gobject gtk3 webkit2gtk4.1" ;;
    *" arch "*) WINDOW_PACKAGES="sudo pacman -S --needed python-gobject gtk3 webkit2gtk-4.1" ;;
    *suse*) WINDOW_PACKAGES="sudo zypper install -y python3-gobject typelib-1_0-Gtk-3_0 typelib-1_0-WebKit2-4_1" ;;
    *) WINDOW_PACKAGES="" ;;
  esac
  if [ -n "$WINDOW_PACKAGES" ] && ask "decompdecomp's window needs GTK and WebKitGTK from your system. Install them now? ($WINDOW_PACKAGES)"; then
    $WINDOW_PACKAGES || echo "  couldn't install them: run $WINDOW_PACKAGES"
  else
    echo "  decompdecomp's window needs PyGObject and WebKitGTK 4.1 (with GObject introspection) from your package manager"
  fi
fi

step "downloading the compilers and tools"
"$BIN/decomp" setup

# an older decompdecomp still running from before this upgrade: stop it, so the new version takes over
"$BIN/decomp" stop >/dev/null 2>&1 || true

# one decompdecomp at a time: an older .deb or .rpm of it would be a second one in the apps menu and on PATH
if [ -d /opt/decompdecomp ]; then
  if command -v dpkg >/dev/null && dpkg -s decompdecomp >/dev/null 2>&1; then REMOVE="sudo apt remove -y decompdecomp"
  elif command -v rpm >/dev/null && rpm -q decompdecomp >/dev/null 2>&1; then REMOVE="sudo dnf remove -y decompdecomp"
  else REMOVE=""; fi
  if [ -n "$REMOVE" ] && ask "An older decompdecomp package is installed too. Remove it? ($REMOVE)"; then
    $REMOVE >/dev/null && step "removed the older decompdecomp package" || echo "  couldn't remove it: run $REMOVE"
  fi
fi

"$BIN/decomp" shortcut >/dev/null 2>&1 && step "added decompdecomp to your apps" || true

bold "Done."
echo "  Start it any time from your apps menu, or with: decomp"
if [ "$START" = 1 ]; then
  if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    nohup "$BIN/decomp" >/dev/null 2>&1 &
    echo "  Opening decompdecomp…"
  else
    echo "  No desktop session found (SSH?). Run 'decomp' on the machine itself."
  fi
fi
