#!/usr/bin/env bash
# Cuts/updates the rolling "nightly" pre-release, mirroring the pattern from
# danijel1124/Disco-A11y: a single tag named "nightly" that gets deleted and
# recreated in place (not a versioned release), marked as a GitHub
# pre-release, carrying a build-identifier version (nightly-YYYYMMDDHHMMSS)
# distinct from the last real numbered release.
#
# Point of this: accumulate small/incremental changes here instead of
# cutting a new vX.Y.Z release for every one of them ("release flooding" -
# see CLAUDE.md's Versioning section for the rule this implements). Cut a
# real numbered release only when deliberately promoting a nightly.
#
# Safe by construction, not by convention: check_for_client_update in
# server/update_check.py only ever auto-detects/pushes the latest *official*
# (non-prerelease, non-draft) release - a nightly is marked prerelease here,
# so it can never get silently auto-pushed to production clients via
# addon_release.json. Use set_addon_release.py by hand if you ever actually
# want to push a nightly build for testing.
#
# Usage: ./make_nightly.sh [notes-file]
#   notes-file: optional path to release notes (markdown). If omitted, a
#   minimal auto-generated body (build id + commit) is used - pass a real
#   changelog file for anything worth remembering later.
set -euo pipefail
cd "$(dirname "$0")"

REPO="danijel1124/nvda-remote"
TAG="nightly"
BUILD_ID="nightly-$(date -u +%Y%m%d%H%M%S)"
COMMIT="$(git rev-parse --short HEAD)"

echo "Building $BUILD_ID from commit $COMMIT..."
cd client
rm -f remote-*.nvda-addon
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
NIGHTLY_VERSION="$BUILD_ID" ../venv/bin/scons
ASSET=$(ls remote-*.nvda-addon)
echo "Built $ASSET"
cd ..

if [ -n "${1:-}" ]; then
	NOTES_FILE="$1"
else
	NOTES_FILE=$(mktemp)
	trap 'rm -f "$NOTES_FILE"' EXIT
	cat > "$NOTES_FILE" <<EOF
**Nightly (untested snapshot)** — build \`$BUILD_ID\`, commit \`$COMMIT\`.

Rolling pre-release: this tag is updated in place, it is not a versioned
release. Server version at this commit: $(grep -oP 'SERVER_VERSION = "\K[^"]+' server/server.py 2>/dev/null || echo "unknown").
EOF
fi

echo "Moving the $TAG tag to $COMMIT..."
git tag -f "$TAG"
git push origin "$TAG" --force

echo "Recreating the GitHub release..."
gh release delete "$TAG" -R "$REPO" --yes --cleanup-tag 2>/dev/null || true
git push origin "$TAG" --force  # --cleanup-tag above may have deleted it remotely
gh release create "$TAG" -R "$REPO" \
	"client/$ASSET" \
	--title "Nightly (untested snapshot)" \
	--notes-file "$NOTES_FILE" \
	--prerelease

echo "Done: https://github.com/$REPO/releases/tag/$TAG"
