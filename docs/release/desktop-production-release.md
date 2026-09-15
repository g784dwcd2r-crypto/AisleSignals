# Desktop production release

## Current guarantee

The repository builds installable **unsigned production candidates** for macOS arm64 and Windows x86-64. They use the stable product name `AisleSignals`, macOS bundle identifier `ie.aislesignals.desktop`, and Windows application ID `DD026687-3AF5-459A-AE9D-187B0482E933`. `packaging/release-identity.json` is the strict identity and version source; CI rejects drift from the three package versions.

An unsigned candidate is suitable for build and installation testing. It is not a public production release. The candidate filename deliberately contains `unsigned`, and no script bypasses Gatekeeper, SmartScreen, signing, notarisation, or certificate verification.

The unsigned macOS CI candidate embeds the staging cloud origin so automated journeys cannot alter production. A signed customer build must receive the reviewed production HTTPS origin explicitly.

The inner PyInstaller directory and private state paths keep the historical `AisleSignalsPilot` name for migration continuity. Changing those paths would orphan existing pairing credentials, SQLite data and startup entries. The installed application, shortcut and macOS bundle use the production identity.

## CI artifacts

The desktop CI job builds and smoke-tests:

- `AisleSignals-macOS-arm64-v<version>-unsigned.dmg`
- `AisleSignals-Windows-x86_64-v<version>-unsigned.exe`
- a SHA-256 sidecar and canonical release index bound to the exact Git commit

`scripts/package_bundle.py` creates byte-reproducible ZIP/TAR archive wrappers from fixed input bytes and the commit timestamp. Native compiler, DMG and Inno Setup outputs are hashed and source-bound; they are not claimed to be byte-reproducible across toolchain images. CI retains candidates for 14 days.

The packaged command `AisleSignals connect` performs explicit laptop enrolment. The one-use code is read without echo. Pairing credentials remain in the existing owner-only local connection file, and the monotonic heartbeat sequence survives restart. Windows uses the native NTFS ACL adapter. The app never stores the one-use code. Startup-on-login starts the application only; staff must still select the CCTV source and arm monitoring.

## Signing preflight

Run the preflight only from the exact clean release commit:

```bash
python scripts/release_preflight.py \
  --platform Darwin \
  --update-private-key-file /private/offline/update-signing-key.pem
```

The equivalent Windows command uses `--platform Windows`. The update key must be a small regular owner-only Ed25519 PEM file. Preflight names missing environment variables or tools without printing their values. It fails on a dirty checkout, a version mismatch, an unexpected source SHA, an unsafe key, a missing signing tool, or incomplete signing credentials.

Set `AISLESIGNALS_RELEASE_SHA` to the reviewed 40-character release commit. The following external inputs remain required.

### macOS

- Apple Developer Program membership
- a `Developer ID Application` certificate and private key installed in the release keychain
- `AISLESIGNALS_APPLE_DEVELOPER_ID` with its codesign identity
- `AISLESIGNALS_APPLE_TEAM_ID` with the ten-character Apple team identifier
- an `xcrun notarytool` keychain profile and its name in `AISLESIGNALS_APPLE_NOTARY_PROFILE`
- current `codesign`, `xcrun`, `hdiutil`, and `spctl` tools

Build the production app with `scripts/build_macos_app.py` and an explicit reviewed production `--cloud-origin`, then run `scripts/sign_notarize_macos.sh APP DMG UPDATE_KEY`. Production packaging refuses an implicit cloud destination. The script preflights before mutation, signs Mach-O contents with hardened runtime and a secure timestamp, signs the app, creates and signs the DMG, waits for Apple notarisation, staples the ticket, and verifies both Gatekeeper assessments. A failed step leaves no publishable result.

### Windows

- an organisation-validated code-signing certificate with private key in the release user's certificate store
- the certificate's exact 40-hex thumbprint in `AISLESIGNALS_WINDOWS_CERT_SHA1`
- Microsoft `signtool`, Inno Setup 6 `iscc`, and timestamp network access

Run `scripts/sign_windows_release.ps1` with the verified bundle, output directory, update-key path and exact version. It signs a temporary copy of the application, compiles `packaging/windows/AisleSignalsProduction.iss` with `SignedBuild=1`, configures Inno's `aislesignals` SignTool so the generated uninstaller and setup program are signed, and verifies the final Authenticode status. It refuses to replace an existing release. The unsigned CI candidate uses `SignedBuild=0` by design.

## Update channel

Generate the Ed25519 update key offline and deploy only its Base64 raw public key through a separately authenticated configuration channel. Never commit the private key. Host installers and manifests at direct HTTPS URLs that do not redirect.

After both operating-system signatures are verified, create the canonical update envelope with `scripts/release_feed.py create`. The installed updater accepts only the exact product/platform/architecture schema, a valid Ed25519 signature from the supplied trusted public key, bounded size, valid source commit, and matching SHA-256 bytes. It downloads to a private directory, never follows redirects, never replaces a mismatched file and never executes an installer automatically. With no trusted public key or valid signed feed, updates fail closed.

## Release gate

Do not publish until macOS and Windows install/uninstall tests pass on supported physical machines, startup behavior survives logout/restart, existing paired clients retain their identity, a revoked client cannot reconnect, updates succeed and fail safely in test channels, and both platform signature/notarisation checks pass. Record the CI run, exact source SHA, artifact index, signature identities and acceptance results in the release record.
