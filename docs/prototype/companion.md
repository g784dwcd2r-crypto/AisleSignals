# Existing-laptop readiness utility

This implemented prototype utility checks the current laptop and, only when explicitly requested, asks one authorised existing RTSP camera for its stream description. It does not collect frames, record video, run a detector, recognise people, play audio, change power settings, scan the network or install anything. Requirements FR-013, FR-059 and FR-067 are partially explored here; their live monitoring and installer acceptance is still pending.

Both Windows and macOS are in the first pilot. The same standard-library Python source supports both families. The tests simulate both platforms and exercise safety/failure paths; this is not evidence of acceptance on either client's laptop. Exact OS versions, architectures and available camera interfaces remain site discovery inputs.

## Run the local checks

From the repository root, using the developer Python 3.11+ environment:

```sh
python -m services.companion
```

On Windows, `py -3 -m services.companion` is an alternative when that launcher is already installed. On macOS, use `python3` if appropriate. Pharmacy staff are not expected to install a Python development environment; this is a developer and commissioning prototype, with packaged installation still to implement.

The JSON report includes the OS family, processor architecture, Python version, available disk bytes, the 5 GB initial disk guard and whether FFprobe is already on PATH. It omits machine names, user names, directory paths, executable paths, camera addresses, environment values and exception text. `--disk-path` can select an existing directory on the volume that future local processing would use; the utility creates no directory or file.

Exit code `0` means these checks completed without a blocker. It does **not** mean monitoring or production readiness. Exit code `2` means an input, platform, disk or requested-probe blocker. Missing FFprobe produces a warning, because the direct RTSP metadata check does not use it. Finding FFprobe does not verify its version, integrity or codec capabilities, and the utility never installs or executes it.

Sleep, lid closure, sign-out and power loss can interrupt a future companion. This utility does not infer whether the laptop is configured to stay awake. Startup, suspend/resume, OS permissions, staff-heard speaker playback, sustained workload, privacy controls and actual camera decode remain manual acceptance work. The current application displays simulated camera coverage independently; this utility does not switch those cameras to live mode.

## Optional one-endpoint protocol check

An authorised operator must already know the camera's permitted stream endpoint. Put its URL into a locally controlled environment variable called `AISLESIGNALS_CAMERA_URL`, without putting a secret in a command argument, screenshot, support ticket or repository file. Then run:

```sh
python -m services.companion --probe-env AISLESIGNALS_CAMERA_URL --authorised
```

The same flags work with the Windows Python launcher. Without both the environment variable name and `--authorised`, no probe runs. A failed local preflight prevents a requested probe. The variable name is validated; accidentally passing the URL as an argument is rejected without echoing it. Ordinary checks make no network request.

This narrow release accepts `rtsp://` or `rtsps://`, a literal RFC1918 IPv4 address or IPv6 unique-local address, port 554/322 or a port from 1024 through 65535, and a plain path. DNS names, public/loopback/link-local addresses, embedded credentials, all query parameters, fragments, percent-encoded paths, traversal and playlist paths are unsupported. A synthetically valid shape is `rtsp://192.168.20.10:554/Streaming/Channels/101`; it does not establish that a real camera exists there. Do not probe this example address unless it is the explicitly authorised existing endpoint.

The utility sends one RTSP 1.0 `DESCRIBE` request over a direct TCP socket. RTSP 2.0-only endpoints are unsupported in this release. It never sends `PLAY`, follows a redirect, opens SDP control addresses, subscribes to audio or reads a video stream. The complete operation has an eight-second deadline, an 8 KiB header bound and a 64 KiB total response bound. Authentication-required responses are reported as unsupported without retrying credentials. **Keep camera authentication enabled.** If access needs a password or signed URL, stop at that limitation; this prototype is not a reason to weaken the existing CCTV system. A future protected credential adapter requires separate implementation and review.

RTSPS uses normal certificate verification, including the literal IP. A self-signed or mismatched certificate fails explicitly; there is no insecure override. Plain RTSP exposes the request path to the existing network, so only use a camera endpoint already approved for that trusted LAN. The utility does not invoke FFmpeg with a URL or expose it in a child-process argument. It still holds the endpoint briefly in process memory; normal OS access controls apply.

`METADATA_REACHABLE` means a bounded RTSP response described video. It does not establish view freshness, decodability, resolution, detection accuracy, universal camera compatibility or continued availability. Response bodies, headers and raw error messages are never returned. An SDP response can contain unrelated addresses, names or tokens; the parser extracts only the fact that video was described and does not use those values as network targets.

## Verification and packaging boundary

Run the standard-library unit suite from the repository root:

```sh
python -m unittest discover -s tests/companion -p 'test_*.py' -v
```

The tests cover the two pilot OS families, disk pressure and read errors, an old interpreter, redaction, absent/explicit authorisation, unsafe URL variants, authentication, redirects, malformed/truncated/oversized responses, timeouts, socket cleanup and TLS verification. All camera interactions in the tests use injected transports or mocked sockets. Running this suite does not contact a camera.

The next distribution stage must build separately on Windows and macOS for the actual pilot architectures, bundle the runtime, apply the respective signing/notarisation process, verify checksums, exercise installation and removal, then test startup and sleep/resume on each existing laptop. No signed installer or always-running service is delivered by this utility. No new hardware is required or proposed.

Protocol references: [RTSP 1.0, RFC 2326, section 10.2](https://www.rfc-editor.org/rfc/rfc2326#section-10.2) defines the description request (RTSP 1.0 is an older protocol, superseded by RTSP 2.0); [FFmpeg protocol documentation](https://ffmpeg.org/ffmpeg-protocols.html#rtsp) and [FFprobe documentation](https://ffmpeg.org/ffprobe.html) distinguish protocol access and stream inspection. This utility's direct protocol check intentionally has a smaller surface than FFprobe. A licensed and tested detector remains a separate integration.
