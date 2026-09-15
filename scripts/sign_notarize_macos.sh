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

work_root=$(mktemp -d "${TMPDIR:-/tmp}/aislesignals-release.XXXXXX")
trap 'rm -rf "$work_root"' EXIT HUP INT TERM
stage="$work_root/stage"
mkdir "$stage"
release_app="$stage/AisleSignals.app"
release_dmg="$work_root/AisleSignals.dmg"
# Sign a private copy. The reviewed input bundle remains byte-for-byte intact,
# so a failed notarisation can never turn it into an ambiguous partial output.
cp -R "$app" "$release_app"

find "$release_app/Contents" -type f \( -perm -111 -o -name '*.dylib' -o -name '*.so' \) -print0 |
  while IFS= read -r -d '' binary; do
    if file "$binary" | grep -q 'Mach-O'; then
      codesign --force --options runtime --timestamp --sign "$AISLESIGNALS_APPLE_DEVELOPER_ID" "$binary"
    fi
  done
codesign --force --deep --options runtime --timestamp --sign "$AISLESIGNALS_APPLE_DEVELOPER_ID" "$release_app"
codesign --verify --deep --strict --verbose=2 "$release_app"
signing_info=$(codesign -dv --verbose=4 "$release_app" 2>&1)
printf '%s\n' "$signing_info" | grep -Fxq "TeamIdentifier=$AISLESIGNALS_APPLE_TEAM_ID" || {
  echo "The signed application team does not match AISLESIGNALS_APPLE_TEAM_ID." >&2
  exit 1
}
spctl --assess --type execute --verbose=2 "$release_app"

ln -s /Applications "$stage/Applications"
hdiutil create -quiet -volname AisleSignals -srcfolder "$stage" -format UDZO "$release_dmg"
codesign --force --timestamp --sign "$AISLESIGNALS_APPLE_DEVELOPER_ID" "$release_dmg"
xcrun notarytool submit "$release_dmg" --keychain-profile "$AISLESIGNALS_APPLE_NOTARY_PROFILE" --wait
xcrun stapler staple "$release_dmg"
xcrun stapler validate "$release_dmg"
spctl --assess --type open --context context:primary-signature --verbose=2 "$release_dmg"
# Publish atomically only after every signature, notarisation and Gatekeeper
# check passed. Failure leaves no ambiguous artifact at the requested path.
mv "$release_dmg" "$dmg"
echo "$dmg"
