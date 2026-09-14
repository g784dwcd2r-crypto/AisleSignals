#!/usr/bin/env python3
"""Connect an existing laptop to AisleSignals management. No camera/media access.

Enrollment is explicit and credentials stay in a private local file. The first
connection reports UNKNOWN camera monitoring: a network heartbeat is not proof
of detection. This attended utility does not install a startup service. Its private-file
adapter currently supports macOS/Linux only; Windows use fails closed.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import platform
import re
import secrets
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request
from uuid import UUID

VERSION = "control-companion-1"


class ConnectionFailure(Exception):
    def __init__(self, code):
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def server_url(value, allow_local=False):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Use an exact HTTPS management address.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path not in {"", "/"} or not parsed.hostname or
                (port is not None and not 1 <= port <= 65535) or
                (parsed.scheme != "https" and not (allow_local and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}))):
            raise ValueError
        if parsed.hostname != "::1" and not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", parsed.hostname):
            raise ValueError
    except ValueError:
        raise ValueError("Use the HTTPS management address without a path or credentials.") from None
    return value.rstrip("/")


class PrivatePathUnsupported(ValueError):
    pass


def private_file(path):
    if os.name == "nt":
        raise PrivatePathUnsupported("The standalone cloud companion requires a verified Windows private-file adapter, which is not available yet. The local detection app and online console remain available.")
    path = Path(path).expanduser().absolute()
    if ".." in path.parts or not path.name or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Choose a private local path without symbolic links.")
    return path


@contextmanager
def _private_parent(path, *, create=False):
    """Held no-follow directory descriptors prevent path replacement redirects.

    Existing permissions are never changed. Root-owned sticky system ancestors
    such as /tmp are allowed, but the final directory must be user-owned and
    not writable by another user/group. Leaf operations use openat semantics.
    """
    path = private_file(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parent.parts[1:]:
            info = os.fstat(descriptor)
            if info.st_uid not in {0, os.geteuid()} or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
                raise ValueError("Use a private directory that other users cannot modify.")
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass  # A competing creator still faces the no-follow open.
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("Use an owner-controlled private directory.")
        yield descriptor, path.name
    finally:
        os.close(descriptor)


def _open_private(directory, name, flags):
    descriptor = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError("Use an owner-only regular file without hard links.")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def write_new(path, data):
    encoded = json.dumps(data).encode("utf-8")
    if len(encoded) > 4096:
        raise ValueError("The connection file is too large.")
    with _private_parent(path, create=True) as (directory, name):
        descriptor = _open_private(directory, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)


def load_connection(path):
    with _private_parent(path) as (directory, name):
        descriptor = _open_private(directory, name, os.O_RDONLY)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("Use a valid private connection file.")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"server", "device_id", "device_token", "allow_local"} or not isinstance(data["allow_local"], bool):
        raise ValueError("Use a valid private connection file.")
    server_url(data["server"], data["allow_local"])
    validate_enrolment_response({"device_id": data["device_id"], "device_token": data["device_token"], "heartbeat_interval_seconds": 30})
    return data


def validate_enrolment_response(value):
    if not isinstance(value, dict) or set(value) != {"device_id", "device_token", "heartbeat_interval_seconds"}:
        raise ConnectionFailure("INVALID_RESPONSE")
    identifier, token, interval = value["device_id"], value["device_token"], value["heartbeat_interval_seconds"]
    try:
        valid_id = isinstance(identifier, str) and str(UUID(identifier)) == identifier
    except ValueError:
        valid_id = False
    if (not valid_id or not isinstance(token, str) or not 32 <= len(token) <= 128 or
            not all(c.isascii() and (c.isalnum() or c in "-_") for c in token) or
            type(interval) is not int or interval != 30):
        raise ConnectionFailure("INVALID_RESPONSE")
    return value


def validate_heartbeat_response(value):
    if not isinstance(value, dict) or set(value) != {"ok", "server_time"} or value["ok"] is not True or not isinstance(value["server_time"], str):
        raise ConnectionFailure("INVALID_RESPONSE")
    try:
        stamp = datetime.fromisoformat(value["server_time"])
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise ConnectionFailure("INVALID_RESPONSE") from None
    return value


def reserve_sequence(path, now_ms=None):
    """Lock, reserve and atomically persist before sending, including restarts.

    The separate stable lock inode coordinates concurrent companion processes;
    replacing the counter itself prevents a crash from leaving a valid-looking
    partial counter. Malformed existing state is preserved and fails closed.
    """
    path = private_file(path)
    import fcntl
    clock = int(time.time() * 1000) if now_ms is None else now_ms
    if type(clock) is not int or clock < 0:
        raise ValueError("The connection clock is invalid.")
    with _private_parent(path) as (directory, name):
        lock = _open_private(directory, name + ".lock", os.O_RDWR | os.O_CREAT)
        temporary = None
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                descriptor = _open_private(directory, name, os.O_RDONLY)
            except FileNotFoundError:
                previous = -1
            else:
                with os.fdopen(descriptor, "rb") as stream:
                    raw = stream.read(65)
                if not re.fullmatch(rb"(?:0|[1-9][0-9]{0,15})\n", raw):
                    raise ValueError("The saved connection sequence is invalid.")
                previous = int(raw)
            sequence = max(previous + 1, clock)
            if sequence > 9007199254740991:
                raise ValueError("The connection sequence is out of range.")
            temporary = "." + name + "." + secrets.token_hex(8)
            descriptor = _open_private(directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(f"{sequence}\n".encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
            os.fsync(directory)
            return sequence
        finally:
            if temporary is not None:
                os.unlink(temporary, dir_fd=directory)
            os.close(lock)


def request(server, route, body, token=None):
    headers = {"Content-Type": "application/json", "User-Agent": "AisleSignals-companion/1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(server + "/device-api/" + route, data=json.dumps(body).encode(), headers=headers, method="POST")
    # Credentials never follow redirects and do not inherit arbitrary proxy settings.
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
    try:
        with opener.open(request, timeout=8) as response:
            data = response.read(65537)
            if len(data) > 65536:
                raise ConnectionFailure("INVALID_RESPONSE")
            return json.loads(data)
    except HTTPError as error:
        raise ConnectionFailure("ACCESS_REVOKED" if error.code in {401, 403} else "SERVICE_UNAVAILABLE") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ConnectionFailure("SERVICE_UNAVAILABLE") from None


def default_path():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AisleSignalsPilot" / "cloud-connection.json"
    return Path.home() / "Library" / "Application Support" / "AisleSignalsPilot" / "cloud-connection.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    enrol = sub.add_parser("enrol", help="Use the one-use code from the online Laptops page")
    enrol.add_argument("--server", required=True)
    enrol.add_argument("--name", required=True, help="Exact laptop name entered in the online console")
    enrol.add_argument("--allow-local", action="store_true", help="Allow loopback HTTP only for a local test console")
    enrol.add_argument("--config", type=Path, default=default_path())
    run = sub.add_parser("run", help="Send attended connection heartbeats; Ctrl+C stops")
    run.add_argument("--config", type=Path, default=default_path())
    run.add_argument("--once", action="store_true", help="Send one connection heartbeat and exit")
    args = parser.parse_args(argv)
    try:
        if args.command == "enrol":
            target = private_file(args.config)
            if target.exists():
                raise ValueError("A connection file already exists. Keep it, or explicitly choose another --config file.")
            server = server_url(args.server, args.allow_local)
            # Reject unsafe/unwritable path ancestry before consuming a one-use
            # server code. The later exclusive creation still handles a race.
            with _private_parent(target, create=True) as (directory, name):
                try:
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError("A connection file already exists.")
            system = "MACOS" if platform.system() == "Darwin" else "WINDOWS" if platform.system() == "Windows" else "OTHER"
            code = getpass.getpass("One-use laptop connection code: ")
            result = validate_enrolment_response(request(server, "enrol", {"token": code, "name": args.name, "platform": system, "app_version": VERSION}))
            write_new(target, {"server": server, "device_id": result["device_id"], "device_token": result["device_token"], "allow_local": args.allow_local})
            print("Laptop registered. Run the companion with this same --config selection to send connection updates. CCTV and alarms remain controlled locally.")
            return 0
        data = load_connection(args.config)
        sequence_path = args.config.with_name(args.config.name + ".sequence")
        while True:
            sequence = reserve_sequence(sequence_path)
            body = {"sequence": sequence, "monitoring_status": "UNKNOWN", "camera_count": 0, "app_version": VERSION}
            try:
                validate_heartbeat_response(request(data["server"], "heartbeat", body, data["device_token"]))
                print("Connected. Camera monitoring status is not reported by this companion.", flush=True)
            except ConnectionFailure as error:
                if error.code == "ACCESS_REVOKED":
                    print("Connection revoked or unavailable to this pharmacy. Request a new connection code.", flush=True)
                    return 1
                print("Management connection unavailable; retrying in 30 seconds. Local CCTV operation is independent.", flush=True)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(30)
    except KeyboardInterrupt:
        print("Connection updates stopped. The dashboard will mark this laptop offline after two minutes.")
        return 0
    except PrivatePathUnsupported as error:
        print(str(error))
        return 1
    except (ValueError, OSError, KeyError, ConnectionFailure):
        print("Connection could not be completed. Check the address, one-use code, laptop details and private configuration file. Existing files were preserved.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
