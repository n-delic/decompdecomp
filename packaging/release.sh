#!/usr/bin/env bash
# A release in one step: the version in its four places (pyproject.toml, src/decompnet/__init__.py,
# site/public/install.sh, uv.lock), the release commit, its tag, pushed. release.yml then builds the packages; it
# stops when the tag's version isn't the code's, as a tag pushed by hand before the bump did.
#
#   packaging/release.sh 1.0.5-alpha "what users get, in one sentence"
set -euo pipefail
version=${1:?usage: packaging/release.sh X.Y.Z[-alpha|-beta|-rc[.N]] \"what users get, in one sentence\"}
what=${2:?say what users get, in one sentence: it becomes the commit subject}
cd "$(dirname "$0")/.."

[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)(\.[0-9]+)?)?$ ]] || { echo "not a version: $version" >&2; exit 1; }
[ "$(git branch --show-current)" = main ] || { echo "releases are made on main" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "commit or stash your changes first" >&2; exit 1; }
git pull --ff-only -q
if git rev-parse -q --verify "refs/tags/v$version" >/dev/null || git ls-remote --exit-code -q --tags origin "v$version" >/dev/null; then
    echo "v$version is tagged already; to make it again: git tag -d v$version; git push origin :refs/tags/v$version" >&2
    exit 1
fi

sed -i "s/^version = \".*\"/version = \"$version\"/" pyproject.toml
sed -i "s/^__version__ = \".*\"/__version__ = \"$version\"/" src/decompnet/__init__.py
sed -i "s/^VERSION=\".*\"/VERSION=\"$version\"/" site/public/install.sh
# uv.lock keeps the version as Python normalises it (1.0.4-alpha.1: 1.0.4a1); `uv lock` would also rewrite the
# whole lock with whatever uv this computer has
pep=$(sed -E 's/-alpha/a/; s/-beta/b/; s/-rc/rc/; s/([abc])\.?([0-9]*)$/\1\2/; s/([ab]|rc)$/\10/' <<<"$version")
sed -i "/^name = \"decompnet\"$/{n;s/^version = \".*\"/version = \"$pep\"/}" uv.lock
git commit -q -m "decompdecomp $version: $what" pyproject.toml src/decompnet/__init__.py site/public/install.sh uv.lock
git tag "v$version"
git push -q origin main "v$version"
echo "v$version pushed: gh run watch \$(gh run list --workflow release --limit 1 --json databaseId -q '.[0].databaseId')"
