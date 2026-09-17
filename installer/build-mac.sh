#!/usr/bin/env bash
# Builds the Packrat macOS app and disk image:
#   dist/Packrat.app
#   dist/installer/Packrat-<version>-macos-<arm64|x86_64>.dmg
#
# Needs Python 3.11-3.13 (python3 on PATH) and Node.js 20+. Runs on the
# Mac it is building for: an Apple Silicon Mac makes the arm64 build, an
# Intel Mac the x86_64 one (GitHub Actions builds both; see
# .github/workflows/installers.yml).
#
# Signing: with no Developer ID the app is ad-hoc signed, which runs on
# Apple Silicon but shows Gatekeeper's "unidentified developer" prompt
# (right-click > Open the first time). Set CODESIGN_IDENTITY to a
# "Developer ID Application: ..." certificate to sign properly, and
# NOTARY_PROFILE (a `notarytool store-credentials` profile name) to also
# notarize and staple, which removes the prompt entirely.
#
#   ./installer/build-mac.sh                    # full build
#   SKIP_FRONTEND=1 ./installer/build-mac.sh    # reuse frontend/dist
#   PACKRAT_VERSION=0.2.0 ./installer/build-mac.sh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
installer="$root/installer"
version="${PACKRAT_VERSION:-$(tr -d '[:space:]' < "$installer/VERSION")}"
export PACKRAT_VERSION="$version"
arch="$(uname -m)"

step() { printf '\n==> %s\n' "$*"; }

if [[ "${SKIP_FRONTEND:-}" != "1" ]]; then
  step "Frontend build"
  unset VITE_API_BASE_URL   # same-origin API in the bundle
  (cd "$root/frontend" && if [[ -f package-lock.json ]]; then npm ci; else npm install; fi && npm run build)
fi

venv="$installer/.venv"
if [[ ! -x "$venv/bin/python" ]]; then
  step "Create build venv"
  python3 -m venv "$venv"
fi
step "Install requirements"
"$venv/bin/python" -m pip install --quiet --upgrade pip
# SQLite only in the bundle: leave the Postgres drivers and pytest out.
bundle_requirements="$(mktemp)"
grep -Ev '^(psycopg2-binary|asyncpg|pytest|pytest-asyncio)\b' "$root/backend/requirements.txt" > "$bundle_requirements"
"$venv/bin/python" -m pip install --quiet -r "$bundle_requirements" -r "$installer/requirements-desktop.txt"
rm -f "$bundle_requirements"

step "PyInstaller"
rm -rf "$root/dist/Packrat.app" "$root/dist/Packrat"
(cd "$root" && "$venv/bin/python" -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller "$installer/packrat.spec")
app="$root/dist/Packrat.app"
[[ -d "$app" ]] || { echo "PyInstaller did not produce $app" >&2; exit 1; }

step "Code signing"
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  codesign --force --deep --options runtime --timestamp --sign "$CODESIGN_IDENTITY" "$app"
else
  echo "CODESIGN_IDENTITY not set: ad-hoc signing (Gatekeeper will ask the user to right-click > Open once)."
  codesign --force --deep --sign - "$app"
fi
codesign --verify --deep --strict "$app"

step "Disk image"
mkdir -p "$root/dist/installer"
dmg="$root/dist/installer/Packrat-$version-macos-$arch.dmg"
staging="$(mktemp -d)"
cp -R "$app" "$staging/"
ln -s /Applications "$staging/Applications"
rm -f "$dmg"
hdiutil create -volname "Packrat" -srcfolder "$staging" -ov -format UDZO "$dmg" >/dev/null
rm -rf "$staging"

if [[ -n "${CODESIGN_IDENTITY:-}" && -n "${NOTARY_PROFILE:-}" ]]; then
  step "Notarizing"
  xcrun notarytool submit "$dmg" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$dmg"
fi

echo
echo "Built $dmg ($(du -h "$dmg" | cut -f1))"
