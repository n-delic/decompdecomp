#!/bin/sh
# Usage: packaging/ps3-gcc/build.sh 1.9.0|2.5.0|2.7.0 [outdir]  -> outdir/ppu-lv2-gcc-<gen>.tar.xz (default: ./dist)
# Needs podman (or docker: DOCKER=docker). Downloads Sony's published GPL source from the sagemono/ps3gcc archive
# at a pinned commit, checks its sha256, then builds in the container (Containerfile).
set -eu
gen=${1:?generation: 1.9.0, 2.5.0 or 2.7.0}; out=${2:-dist}; here=$(cd "$(dirname "$0")" && pwd)
case $gen in
  1.9.0) commit=8bc057a8fb9e554bafffe2fffe2b4aebb2a42128 sha=9a83d9624e36d833146e8d01a309ddfd48f85145f66bc7fae635052b56f8b952 ;;
  2.5.0) commit=7dbd09a3f40e1e3db0b435cbdb0c6750e923f834 sha=505af001ca4d218c7bd1180848887a9b497a7aba1056eda41be09ad50ed238f8 ;;
  2.7.0) commit=dc6c3b99344249e292e03354f8d8d20d225b0f50 sha=04adc105babd4a7919423f7cb52ac1409b3ab2671e00484a026e56e6afcfa322 ;;
  *) echo "unknown generation $gen" >&2; exit 1 ;;
esac
ctx=$(mktemp -d); trap 'rm -rf "$ctx"' EXIT
curl -fsSL -o "$ctx/src.tar.gz" "https://codeload.github.com/sagemono/ps3gcc/tar.gz/$commit"
echo "$sha  $ctx/src.tar.gz" | sha256sum -c -
# Only the GPL parts go into the pack. Sony's own files in the archive (its readme, its build scripts, and the vector
# intrinsics headers it added to GCC's tree) say "SCE CONFIDENTIAL ... All Rights Reserved": they aren't GPL, so they
# are left out, found by that notice. The build doesn't need the scripts (the Containerfile builds directly); GCC's
# configuration lists the headers, so each becomes a stub that stops a file including it: that file needs the
# header of the user's own SDK (decompdecomp's PS3 SDK kit).
mkdir "$ctx/src" && tar xzf "$ctx/src.tar.gz" -C "$ctx/src" --strip-components=1 && rm "$ctx/src.tar.gz"
removed=$(cd "$ctx/src" && grep -rl "SCE CONFIDENTIAL" . | sort)
for f in $removed; do
  case $f in
    *.h) printf '#error "%s is Sony'"'"'s, not GPL: it is left out of this compiler. Use your own PS3 SDK'"'"'s."\n' \
           "$(basename "$f")" > "$ctx/src/$f" ;;
    *) rm "$ctx/src/$f" ;;
  esac
done
tar czf "$ctx/src.tar.gz" -C "$ctx" --transform "s|^src|ps3gcc-$gen|" src && rm -rf "$ctx/src"
cat > "$ctx/SOURCE.txt" <<EOT
ppu-lv2-gcc, SDK generation $gen: GCC 4.1.1 and binutils 2.17 with Sony's changes, built from the GPL source of
Sony's toolchain-src-$gen-GCC411, archived at https://github.com/sagemono/ps3gcc commit $commit (sha256 $sha).
That source is in ps3gcc-source.tar.gz beside this file, less Sony's own files marked "SCE CONFIDENTIAL" (not
GPL; the headers among them are stubs that stop a file including them):
$(echo "$removed" | sed 's|^\./|  |')
GCC and binutils are free software under the GNU GPL (COPYING, COPYING.LIB, COPYING.binutils here); you may
redistribute and change them under those terms. Built for decompdecomp by packaging/ps3-gcc/.
EOT
cp "$here/Containerfile" "$ctx/"
${DOCKER:-podman} build --build-arg GEN="$gen" --target out -o "type=local,dest=$out" "$ctx"
sha256sum "$out/ppu-lv2-gcc-$gen.tar.xz"
