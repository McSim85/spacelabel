#!/usr/bin/env bash
# Ad-hoc code-sign a spacelabel.app bundle INSIDE-OUT (nested Mach-O first, outer last).
#
# A py2app bundle embeds Python.framework + dylibs + extension modules. `codesign
# --deep` is tolerable for ad-hoc but wrong for notarization, so we sign each nested
# binary first, then the outer bundle (todo/phase-6-blockers.md Tier 1 step 6). Ad-hoc
# (`--sign -`) needs no Apple Developer account, no secrets, and is fully automatable in
# CI; the OUTER bundle carries `--identifier dev.mcsim.spacelabel` so TCC/Accessibility
# keys on that stable, named identity (the whole point of the bundle).
#
# Caveat (documented in README/UI.md): an ad-hoc cdhash changes on every rebuild, so the
# Accessibility grant drops on reinstall/upgrade until re-granted. Developer-ID signing +
# notarization (a paid, deferred follow-on) would make the grant durable.
#
# Usage: tools/codesign_app.sh <path-to-spacelabel.app>
set -euo pipefail

APP="${1:?usage: codesign_app.sh <path-to-spacelabel.app>}"
IDENTIFIER="${SPACELABEL_BUNDLE_ID:-dev.mcsim.spacelabel}"

[ -d "$APP" ] || { echo "no such bundle: $APP" >&2; exit 1; }
echo "ad-hoc signing (inside-out) as ${IDENTIFIER}: $APP"

# 1. Nested dylibs and extension modules (.so). These are leaf Mach-O files (none
# contains another signable item), so order among them is irrelevant -- the inside-out
# invariant is enforced by the STEP order below (leaves here, frameworks next, outer
# bundle last), not by sorting. No `sort` (its `-z` is non-portable across runners).
find "$APP/Contents" \( -name '*.dylib' -o -name '*.so' \) -type f -print0 \
  | while IFS= read -r -d '' lib; do
      codesign --force --sign - "$lib"
    done

# 2. Embedded Python.framework: sign each versioned bundle, then the umbrella.
if [ -d "$APP/Contents/Frameworks/Python.framework" ]; then
  while IFS= read -r -d '' ver; do
    codesign --force --sign - "$ver"
  done < <(find "$APP/Contents/Frameworks/Python.framework/Versions" \
             -mindepth 1 -maxdepth 1 -type d -print0)
  codesign --force --sign - "$APP/Contents/Frameworks/Python.framework"
fi

# 3. Helper executables in MacOS/ (the main `spacelabel` exe is signed by the outer pass).
for exe in "$APP/Contents/MacOS/"*; do
  [ -e "$exe" ] || continue  # nullglob guard: an empty MacOS/ leaves the literal glob
  [ "$(basename "$exe")" = "spacelabel" ] && continue
  codesign --force --sign - "$exe"
done

# 4. Outer bundle LAST, with the stable identifier (the TCC identity).
codesign --force --sign - --identifier "$IDENTIFIER" "$APP"

echo "--- codesign -dvvv ---"
codesign -dvvv "$APP" 2>&1 | grep -iE 'Identifier|Signature|Format|TeamIdent' || true
echo "--- verify --deep --strict ---"
codesign --verify --deep --strict --verbose=2 "$APP"
echo "OK: signed $APP ad-hoc as ${IDENTIFIER}"
