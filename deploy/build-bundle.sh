#!/usr/bin/env bash
#
# Builds the web app and packs everything the server runs into one tarball:
#
#   deploy/build-bundle.sh [out.tar.gz]        # default: ./astrolabe.tar.gz
#
# CI runs this on every push to main; it works the same by hand. The Python
# side comes from `git archive HEAD`, so only committed files ship -- never a
# local raster, a config/locations.local.yaml, or anything else lying in the
# working tree. The sky-brightness map is not in the bundle either: it lives
# on the server (deploy/README.md), outside every release.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(realpath -m "${1:-astrolabe.tar.gz}")"

echo "==> Building the web app"
(cd "$ROOT/web" && npm ci --no-audit --no-fund && npm run build)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "==> Staging"
git -C "$ROOT" archive --format=tar HEAD \
    engine api cli db pyproject.toml LICENSE THIRD_PARTY_NOTICES.md \
    config/locations.yaml config/equipment.yaml \
  | tar -x -C "$STAGE"
mkdir -p "$STAGE/web"
cp -r "$ROOT/web/dist" "$STAGE/web/dist"

# Belt and braces: none of these can arrive through git archive, and none may
# ever leave this machine in a bundle.
for f in config/skybrightness.tif config/skybrightness_tiles config/locations.local.yaml; do
  if [ -e "$STAGE/$f" ]; then
    echo "FATAL: $f is in the bundle" >&2
    exit 1
  fi
done

tar -czf "$OUT" -C "$STAGE" .
echo "==> $OUT ($(du -h "$OUT" | cut -f1))"
