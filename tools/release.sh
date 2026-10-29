#!/usr/bin/env bash
# Release this repository: tag origin/main, or origin/release/X.Y for a patch
# to an older line X.Y, with a version and push the tag.
# .github/workflows/release.yml then builds and publishes it.
#
#   tools/release.sh 3.2.0b1   # pre-release
#   tools/release.sh 3.2.0     # stable release, Latest if it is the highest
#
# A plain `git tag X && git push origin X` works too. This script only adds
# checks first: the shape of the version, the binary-frame floor, a clean and
# current branch, and a version higher than every earlier release tag (of its
# line, on release/X.Y).
set -euo pipefail

VERSION="${1:-}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+((b|rc)[0-9]+)?$ ]]; then
    echo "usage: $0 X.Y.Z | X.Y.ZbN | X.Y.ZrcN" >&2
    exit 2
fi

cd "$(git rev-parse --show-toplevel)"
git fetch --quiet --tags origin

# release/X.Y exists only for a line that main has left behind.
LINE="${VERSION%.*}"
if git rev-parse -q --verify "refs/remotes/origin/release/$LINE" >/dev/null; then
    BRANCH="release/$LINE"
    PATTERN="$LINE.*"
else
    BRANCH=main
    PATTERN='[0-9]*'
fi

# A commit that carries the binary command frame must not be tagged below its
# first feature version: a JSON-era CLI of that feature version would install
# the firmware and then could not drive it. A commit without the frame has no
# floor. The floor comes from the branch that is tagged, so a patch on
# release/X.Y (no frame) is not judged by main's floor. release.yml makes the
# same check on the tagged commit.
FLOOR=$(git show "origin/$BRANCH:firestarter/command_frame.py" 2>/dev/null | sed -n 's/^MIN_FEATURE = (\([0-9]*\), \([0-9]*\))$/\1.\2/p' || true)
if [ -n "$FLOOR" ]; then
    IFS=. read -r V_MAJOR V_MINOR _ <<<"$VERSION"
    IFS=. read -r F_MAJOR F_MINOR <<<"$FLOOR"
    if (( V_MAJOR < F_MAJOR || (V_MAJOR == F_MAJOR && V_MINOR < F_MINOR) )); then
        echo "error: $VERSION is below $FLOOR, the first version of the binary command frame" >&2
        echo "       on origin/$BRANCH. A JSON-era CLI of version $V_MAJOR.$V_MINOR would install it." >&2
        exit 1
    fi
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "error: the working tree is not clean" >&2
    exit 1
fi
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
    echo "error: tag $VERSION already exists" >&2
    exit 1
fi

# PEP 440 order: 3.1.0b8 < 3.1.0 < 3.1.1b1. A new tag must sort above every
# earlier release tag, or pip and `firestarter fw` never offer it.
LATEST=$(git tag --list "$PATTERN" | python3 -c '
import sys
from packaging.version import InvalidVersion, Version
vs = []
for line in sys.stdin:
    try:
        vs.append(Version(line.strip()))
    except InvalidVersion:
        pass
print(max(vs) if vs else "")
')
if [ -n "$LATEST" ] && ! python3 -c "
import sys
from packaging.version import Version
sys.exit(0 if Version('$VERSION') > Version('$LATEST') else 1)
"; then
    echo "error: $VERSION does not sort above the latest tag $LATEST" >&2
    exit 1
fi

TARGET=$(git rev-parse "origin/$BRANCH")
echo "Tag $VERSION on origin/$BRANCH $(git log -1 --format='%h %s' "$TARGET")"
read -r -p "Push the tag and publish? [y/N] " ANSWER
if [ "$ANSWER" != "y" ]; then
    echo "Nothing pushed."
    exit 1
fi

git tag -a "$VERSION" -m "$VERSION" "$TARGET"
git push origin "refs/tags/$VERSION"

if command -v gh >/dev/null; then
    echo "Waiting for the release run to start..."
    sleep 10
    RUN_ID=$(gh run list --workflow release.yml --event push --limit 5 \
        --json databaseId,headBranch --jq ".[] | select(.headBranch == \"$VERSION\") | .databaseId" | head -1)
    if [ -n "$RUN_ID" ]; then
        gh run watch "$RUN_ID" --exit-status
    else
        echo "Run not found yet. Follow it with: gh run list --workflow release.yml"
    fi
fi
