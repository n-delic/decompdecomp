#!/usr/bin/env bash
# A release in one step: the version in its four places (pyproject.toml, src/decompnet/__init__.py,
# site/public/install.sh, uv.lock), the release commit, its tag, pushed. release.yml then builds the packages; it
# stops when the tag's version isn't the code's, as a tag pushed by hand before the bump did.
#
#   packaging/release.sh 1.0.5-alpha "what users get, in one sentence"
set -euo pipefail
version=${1:?usage: packaging/release.sh X.Y.Z[-alpha|-beta|-rc|-rr[.N]] \"what users get, in one sentence\"}
what=${2:?say what users get, in one sentence: it becomes the commit subject}
cd "$(dirname "$0")/.."

[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)(\.[0-9]+)?|-rr\.[0-9]+)?$ ]] || { echo "not a version: $version" >&2; exit 1; }
[ "$(git branch --show-current)" = main ] || { echo "releases are made on main" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "commit or stash your changes first" >&2; exit 1; }
git pull --ff-only -q
if git rev-parse -q --verify "refs/tags/v$version" >/dev/null || git ls-remote --exit-code -q --tags origin "v$version" >/dev/null; then
    echo "v$version is tagged already; to make it again: git tag -d v$version; git push origin :refs/tags/v$version" >&2
    exit 1
fi

# Keep tag/display spelling separate from Python's PEP 440 metadata spelling.
python3 - "$version" <<'PYVERSION'
import pathlib
import re
import sys

version = sys.argv[1]
pep = version.replace("-rr.", "+rr.")
for suffix, short in (("alpha", "a"), ("beta", "b"), ("rc", "rc")):
    pep = re.sub(r"-" + suffix + r"(?:[.](\d+))?$", lambda m: short + (m[1] or "0"), pep)
replacements = {
    "pyproject.toml": (r'^version = ".*"', f'version = "{pep}"'),
    "src/decompnet/__init__.py": (r'^__version__ = ".*"', f'__version__ = "{version}"'),
    "site/public/install.sh": (r'^VERSION=".*"', f'VERSION="{version}"'),
    "uv.lock": (r'(name = "decompnet"\n)version = ".*"', lambda m: m[1] + f'version = "{pep}"'),
}
for filename, (pattern, replacement) in replacements.items():
    path = pathlib.Path(filename)
    updated, count = re.subn(pattern, replacement, path.read_text(), count=1, flags=re.M)
    if count != 1:
        raise SystemExit(f"Missing version in {filename}")
    path.write_text(updated)
PYVERSION
git commit -q -m "decompdecomp $version: $what" pyproject.toml src/decompnet/__init__.py site/public/install.sh uv.lock
git tag "v$version"
git push -q origin main "v$version"
echo "v$version pushed: gh run watch \$(gh run list --workflow release --limit 1 --json databaseId -q '.[0].databaseId')"
