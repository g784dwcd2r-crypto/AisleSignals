# macOS packaged runtime acceptance

Date: 14 September 2026. Source: `e3e731e0a914632db4a8baf82ed53501f9f0a5ac` on
`codex/macos-runtime-acceptance`. Host: macOS 26.6.2 (`25G83`), Apple silicon arm64.
This pass used a new local directory, synthetic account details and no customer credentials or footage.

## Result

The unsigned arm64 pilot bundle builds, starts, serves its local interface, enforces private first-owner
setup, exposes the two-stage cloud pairing boundary, stops cleanly and starts again with prior sessions
invalidated. No packaging or runtime defect was reproduced, so this pass changes documentation only.

This is a **PASS for an unsigned casework-only development bundle on this Mac**. It is not a signed or
notarised installer and is not accepted for pharmacy deployment. CCTV capture, model inference, speaker
audibility, sleep/wake, login-item startup and real cloud pairing remain site or credential dependent.

## Commands and observed results

Toolchain inventory:

```text
git rev-parse HEAD
# e3e731e0a914632db4a8baf82ed53501f9f0a5ac
sw_vers
# ProductVersion 26.6.2; BuildVersion 25G83
uname -m
# arm64
python --version; python -m PyInstaller --version; node --version; npm --version
# Python 3.12.14; PyInstaller 6.22.2; Node v26.8.2; npm 11.19.1
```

Repository release procedure:

```text
npm ci --prefix apps/web
npm run build
# PASS: TypeScript and Vite production build; 1,902 modules transformed.

python -m PyInstaller --clean --noconfirm packaging/pilot.spec
# PASS: arm64 executable and AisleSignalsPilot directory built.

python scripts/package_bundle.py --bundle-name AisleSignalsPilot --smoke
# PASS: frozen sender spawn/READY/exit, startup, assets, account tools, private owner setup,
# Administration, no demo identity, named login, supervised runtime fence, JPEG job and disabled provider.

(cd dist && shasum -a 256 -c AisleSignalsPilot-Darwin-arm64-unsigned.tar.gz.sha256)
# PASS
```

The release manifest records `source_has_uncommitted_changes: false`, `platform: Darwin`,
`architecture: arm64` and `signed: false`. The bundle contains 90 files. An inventory scan found no
database and no `.mp4`, `.mov`, `.webm`, `.avi`, `.mkv`, `.jpg` or `.jpeg` file.

Live launch used an isolated private directory and ports because another local AisleSignals process owned
the defaults. The launcher correctly refused to adopt those processes during preflight.

```text
mkdir -p dist/macos-acceptance-data
chmod 700 dist/macos-acceptance-data
dist/AisleSignalsPilot/AisleSignalsPilot \
  --data-dir dist/macos-acceptance-data --port 18865 --casework-only --no-browser
# PASS: "Application available for casework" and loopback URL reported.

curl http://127.0.0.1:18865/api/health
# 200 {"status":"ok","mode":"pilot","version":"0.1.0"}

curl http://127.0.0.1:18865/
# 200; bundled HTML, JavaScript and CSS references present; restrictive local CSP and no-store headers present.
```

The unprovisioned database reported `setup.available=true` and required a 15-minute one-use token.
The known demo login `manager@harbour.demo` / `AisleDemo!2026` returned `401 INVALID_CREDENTIALS` when
sent with the required local Origin header. A synthetic owner was created through the one-use setup flow,
then named login returned role `MANAGER`. Ephemeral setup, password, cookie and CSRF values were not saved
in this report.

Authenticated `GET /api/cloud-connection` returned `enabled=true`, no current connection and monitoring
`UNKNOWN`. The interface and API require all of the following before preparation: a path-free HTTPS cloud
origin, a 43-character one-use connection code, a laptop name, current manager password, CSRF token and
manager/site authority. A malformed preparation returned `422 INVALID_INPUT` before registration. The
test stopped there because no real cloud credential was supplied. Confirmation remains a separate step
that rechecks the remote identity and exact local branch before activation.

Ctrl+C produced `Launcher stopped its owned services`. Curl then failed to connect, `lsof` found no
listener on 18865 and no matching API child remained. Restarting the same command returned health 200 and
`SERVICES_READY`, preserved one synthetic named user, retained zero demo users, made first-owner setup
unavailable and showed zero active sessions. A second Ctrl+C again left no listener.

Signing inspection:

```text
codesign -dv --verbose=2 dist/AisleSignalsPilot/AisleSignalsPilot
# Signature=adhoc; TeamIdentifier=not set

spctl -a -vv dist/AisleSignalsPilot/AisleSignalsPilot
# rejected (exit 3)
```

This rejection is expected for the explicitly unsigned artifact. Distribution still requires an owner
supplied Apple Developer ID, hardened-runtime signing, notarisation and validation of the final delivered
package on each supported pharmacy Mac.

## Remaining acceptance

- Pair against the intended production HTTPS service using a fresh one-use code, review the returned
  organisation/pharmacy/device identity, confirm it, and verify pause/resume/disconnect and offline replay.
- Inventory the pharmacy Mac model, OS version and permissions; test the actual authorised CCTV viewer,
  four/six-camera layout, source reconnection, camera timestamp continuity and sustained load.
- Install the pinned vision runtime and weights, then measure held-out branch interaction performance.
- Hear and acknowledge the alarm on the pharmacy speakers, including Focus, minimise and screen-lock cases.
- Test sleep/wake re-arm, approved startup on login, update, rollback and uninstall on the target laptop.
- Sign and notarise the final package. The current archive must remain labelled unsigned and must not be
  represented as a commissioned installer.
