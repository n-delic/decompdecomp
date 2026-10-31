#!/bin/sh
# Usage: packaging/ps3-gcc/build.sh 1.9.0|2.7.0 [outdir]  -> outdir/ppu-lv2-gcc-<gen>.tar.xz (default: ./dist)
# Needs podman (or docker: DOCKER=docker). Downloads Sony's published GPL source from the sagemono/ps3gcc archive
# at a pinned commit, checks its sha256, then builds in the container (Containerfile).
set -eu
gen=${1:?generation: 1.9.0 or 2.7.0}; out=${2:-dist}; here=$(cd "$(dirname "$0")" && pwd)
case $gen in
  1.9.0) commit=8bc057a8fb9e554bafffe2fffe2b4aebb2a42128 sha=9a83d9624e36d833146e8d01a309ddfd48f85145f66bc7fae635052b56f8b952 ;;
  2.7.0) commit=dc6c3b99344249e292e03354f8d8d20d225b0f50 sha=04adc105babd4a7919423f7cb52ac1409b3ab2671e00484a026e56e6afcfa322 ;;
  *) echo "unknown generation $gen" >&2; exit 1 ;;
esac
ctx=$(mktemp -d); trap 'rm -rf "$ctx"' EXIT
curl -fsSL -o "$ctx/src.tar.gz" "https://codeload.github.com/sagemono/ps3gcc/tar.gz/$commit"
echo "$sha  $ctx/src.tar.gz" | sha256sum -c -
cat > "$ctx/SOURCE.txt" <<EOT
ppu-lv2-gcc, SDK generation $gen: GCC 4.1.1 and binutils 2.17 with Sony's changes, built unmodified from
the GPL source Sony published (toolchain-src-$gen-GCC411), archived at
https://github.com/sagemono/ps3gcc commit $commit.
That exact source is in ps3gcc-source.tar.gz beside this file (sha256 $sha).
GCC and binutils are free software under the GNU GPL (COPYING, COPYING.LIB, COPYING.binutils here); you may
redistribute and change them under those terms. Built for decompdecomp by packaging/ps3-gcc/.
EOT
cp "$here/Containerfile" "$ctx/"
${DOCKER:-podman} build --build-arg GEN="$gen" --target out -o "type=local,dest=$out" "$ctx"
sha256sum "$out/ppu-lv2-gcc-$gen.tar.xz"
