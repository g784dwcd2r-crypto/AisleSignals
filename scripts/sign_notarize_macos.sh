#!/bin/bash
set -eu

usage() { echo "usage: sign_notarize_macos.sh APP DMG_OUTPUT UPDATE_PRIVATE_KEY" >&2; exit 2; }
[ "$#" -eq 3 ] || usage
app=$1
dmg=$2
update_key=$3

# The Python preflight checks every credential and tool before this script can
# mutate the application or create an artifact. Values are never echoed.
python3 scripts/release_preflight.py --platform Darwin --update-private-key-file "$update_key"
[ -d "$app" ] || { echo "The macOS application bundle is missing." >&2; exit 1; }
[ ! -e "$dmg" ] || { echo "Refusing to replace an existing DMG." >&2; exit 1; }

find "$app/Contents" -type f \( -perm -111 -o -name '*.dylib' -o -name '*.so' \) -print0 |
  while IFS= read -r -d '' binary; do
    if file "$binary" | grep -q 'Mach-O'; then
      codesign --force --options runtime --timestamp --sign "$AISLESIGNALS_APPLE_DEVELOPER_ID" "$binary"
    fi
  done
codesign --force --deep --options runtime --timestamp --sign "$AISLESIGNALS_APPLE_DEVELOPER_ID" "$app"
codesign --verify --deep --strict --verbose=2 "$app"
spctl --assess --type execute --verbose=2 "$app"

stage=$(mktemp -d "${TMPDIR:-/tmp}/aislesignals-release.XXXXXX")
trap 'rm -rf "$stage"' EXIT HUP INT TERM
cp -R "$app" "$stage/AisleSignals.app"
ln -s /Applications "$stage/Applications"
hdiutil create -quiet -volname AisleSignals -srcfolder "$stage" -format UDZO "$dmg"
codesign --force --timestamp --sign "$AISLESIGNALS_APPLE_DEVELOPER_ID" "$dmg"
xcrun notarytool submit "$dmg" --keychain-profile "$AISLESIGNALS_APPLE_NOTARY_PROFILE" --wait
xcrun stapler staple "$dmg"
xcrun stapler validate "$dmg"
spctl --assess --type open --context context:primary-signature --verbose=2 "$dmg"
echo "$dmg"
