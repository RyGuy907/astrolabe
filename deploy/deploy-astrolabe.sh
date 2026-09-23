#!/usr/bin/env bash
#
# Server-side half of a deploy. AWS SSM runs it as the ubuntu user with an
# s3:// URL to a bundle made by deploy/build-bundle.sh:
#
#   deploy-astrolabe.sh s3://<deploy-bucket>/astrolabe/<sha>.tar.gz
#
# The copy that runs lives on the instance at ~/bin/deploy-astrolabe.sh.
# Changing this file does not update it; deploy/README.md has the command
# that does. (Named apart from norhog's ~/bin/deployFromS3.sh on the same box.)
#
# The API is installed and restarted first, and the web app is published only
# once the API answers, so a new page never talks to an old API. If the API
# does not come up, the previous release is put back.
set -uo pipefail

ARTIFACT="${1:?usage: deploy-astrolabe.sh s3://bucket/key.tar.gz}"
DEST="$HOME/services/astrolabe"
VENV="$HOME/venvs/astrolabe"
WEB=/var/www/astrolabe
HEALTH="http://127.0.0.1:8001/api/health"

for cmd in aws curl tar rsync sudo; do
  command -v "$cmd" >/dev/null || { echo "FATAL: $cmd not found on PATH" >&2; exit 1; }
done
[ -x "$VENV/bin/pip" ] || { echo "FATAL: no virtualenv at $VENV (deploy/README.md, one-time setup)" >&2; exit 1; }
[ -w "$WEB" ] || { echo "FATAL: $WEB missing or not writable (deploy/README.md, one-time setup)" >&2; exit 1; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "==> Downloading $ARTIFACT"
aws s3 cp "$ARTIFACT" "$TMP/artifact.tar.gz" --only-show-errors || exit 1

echo "==> Extracting"
mkdir -p "$TMP/new"
tar -xzf "$TMP/artifact.tar.gz" -C "$TMP/new" || exit 1
for f in api/main.py pyproject.toml web/dist/index.html; do
  [ -f "$TMP/new/$f" ] || { echo "FATAL: bundle has no $f" >&2; exit 1; }
done

# One previous release is kept so a failed start can roll back.
echo "==> Swapping in the new release"
mkdir -p "$HOME/services"
if [ -d "$DEST" ]; then
  rm -rf "$DEST.prev"
  mv "$DEST" "$DEST.prev"
fi
mv "$TMP/new" "$DEST"

# The API answers its health check. Startup loads the catalogue, which takes a
# few seconds, so it gets up to a minute.
healthy() {
  for _ in $(seq 1 30); do
    curl -fsS -m 5 -o /dev/null "$HEALTH" && return 0
    sleep 2
  done
  return 1
}

# Editable, so the imports -- and the config/ found beside them -- are the
# release directory's. $DEST is the same path after every swap.
install_and_start() {
  echo "==> Installing $1"
  (cd "$1" && "$VENV/bin/pip" install --quiet --disable-pip-version-check -e ".[skybrightness]") || return 1
  echo "==> Restarting"
  sudo -n systemctl restart astrolabe || return 1
  healthy
}

# Old hashed assets are kept for a while: a page loaded before the deploy may
# still ask for them. Current ones were just copied, so they are never old.
publish_web() {
  echo "==> Publishing the web app"
  rsync -a "$1/web/dist/" "$WEB/" || return 1
  find "$WEB/assets" -type f -mtime +14 -delete 2>/dev/null || true
}

rollback() {
  if [ ! -d "$DEST.prev" ]; then
    echo "!!! No previous release to roll back to: the site is down" >&2
    return 1
  fi
  echo "!!! Rolling back to the previous release" >&2
  rm -rf "$DEST"
  mv "$DEST.prev" "$DEST"
  if install_and_start "$DEST" && publish_web "$DEST"; then
    echo "!!! Rollback succeeded: the site is serving the previous release" >&2
    return 0
  fi
  echo "!!! ROLLBACK ALSO FAILED: the site is down and needs manual attention" >&2
  return 1
}

if install_and_start "$DEST" && publish_web "$DEST"; then
  echo "==> OK: the API is answering on 8001 and the web app is published"
  rm -rf "$DEST.prev"
  exit 0
fi

echo "!!! Deploy FAILED. The service's last log lines:" >&2
journalctl -u astrolabe -n 40 --no-pager >&2 || true
rollback
exit 1
