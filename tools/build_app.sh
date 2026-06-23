#!/usr/bin/env bash
# Build the self-contained spacelabel.app bundle via py2app (build-time only).
#
# py2app is a BUILD-time dependency only -- never added to the project's runtime deps
# (todo/phase-6-blockers.md Tier 1 step 2). This script is used both locally and by the
# release workflow (.github/workflows): it creates an isolated build venv, builds the
# icon, runs py2app into ./dist, writes the CLI shim, and ad-hoc code-signs the bundle
# inside-out so the agent process carries the dev.mcsim.spacelabel TCC identity.
#
# Signing is ALWAYS done: writing the CLI shim after py2app invalidates py2app's own
# signature, so a re-sign is mandatory for a valid (launchable, Gatekeeper-clean) bundle.
#
# Usage:
#   tools/build_app.sh            # build + ad-hoc sign (the legacy --sign flag is accepted + ignored)
#
# Requires: uv + macOS (sips/iconutil/codesign). Output: dist/spacelabel.app
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY_VERSION="${SPACELABEL_PY_VERSION:-3.14}"
BUILD_VENV="$REPO_ROOT/.venv-build"
DIST_DIR="$REPO_ROOT/dist"
BUILD_BASE="$REPO_ROOT/build/py2app"
MASTER="$REPO_ROOT/packaging/icon/spacelabel-1024.png"
ICNS="$REPO_ROOT/packaging/icon/spacelabel.icns"
APP="$DIST_DIR/spacelabel.app"

echo "==> isolated build venv ($BUILD_VENV, python $PY_VERSION)"
# --clear so re-runs (and CI) start from a clean, reproducible env rather than failing
# on a pre-existing .venv-build.
uv venv --clear --python "$PY_VERSION" "$BUILD_VENV"
# Non-editable install so py2app bundles the built package (not a src/ link), plus the
# build-time-only py2app. PyObjC + click come in as the project's runtime deps.
uv pip install --python "$BUILD_VENV/bin/python" "$REPO_ROOT" py2app

echo "==> icon (generates a placeholder master if none is checked in)"
"$BUILD_VENV/bin/python" "$REPO_ROOT/tools/make_icon.py" --master "$MASTER" --icns "$ICNS"

echo "==> py2app build"
rm -rf "$APP"
(
  cd "$REPO_ROOT/packaging/py2app"
  SPACELABEL_ICNS="$ICNS" "$BUILD_VENV/bin/python" setup.py py2app \
    --dist-dir "$DIST_DIR" --bdist-base "$BUILD_BASE"
)

[ -d "$APP" ] || { echo "build did not produce $APP" >&2; exit 1; }

# CLI shim (the cask `binary` stanza symlinks THIS onto PATH). It must exec the py2app
# main executable by its ABSOLUTE in-bundle path: running the stub through a symlink makes
# its @executable_path resolve to the symlink's directory rather than the bundle, which
# breaks the embedded-Python runtime lookup. The shim resolves its own (possibly
# symlinked) location and execs the sibling MacOS/ stub. The login agent is unaffected
# (the LaunchAgent runs the stub by absolute path).
echo "==> CLI shim (Contents/Resources/spacelabel)"
CLI_SHIM="$APP/Contents/Resources/spacelabel"
cat > "$CLI_SHIM" <<'SHIM'
#!/bin/sh
target="$0"
while [ -L "$target" ]; do
  link="$(readlink "$target")"
  case "$link" in
    /*) target="$link" ;;
    *) target="$(dirname "$target")/$link" ;;
  esac
done
dir="$(cd "$(dirname "$target")" && pwd)"
exec "$dir/../MacOS/spacelabel" "$@"
SHIM
chmod +x "$CLI_SHIM"

# ALWAYS re-sign: adding the shim above invalidated py2app's own signature, so an
# unsigned-stale bundle would fail codesign --verify and Gatekeeper. Inside-out ad-hoc.
echo "==> ad-hoc codesign (inside-out)"
"$REPO_ROOT/tools/codesign_app.sh" "$APP"

echo "==> built: $APP"
echo -n "version: "
"$APP/Contents/MacOS/spacelabel" --version

# Regression guard: the CLI must work when invoked via a symlink (how the cask exposes it).
echo "==> CLI-via-symlink self-test"
SHIM_LINK="$(mktemp -u "${TMPDIR:-/tmp}/spacelabel-shimtest.XXXXXX")"
ln -s "$CLI_SHIM" "$SHIM_LINK"
if perl -e 'alarm 20; exec @ARGV' "$SHIM_LINK" --version >/dev/null 2>&1; then
  echo "    OK: CLI works via symlink"
  rm -f "$SHIM_LINK"
else
  echo "    FAIL: CLI broken via symlink (py2app @executable_path regression)" >&2
  rm -f "$SHIM_LINK"
  exit 1
fi
