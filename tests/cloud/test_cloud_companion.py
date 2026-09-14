"""Companion boundary tests: synthetic files and process-owned loopback servers."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import ssl
import stat
import threading
from uuid import uuid4

import pytest

from scripts import cloud_companion as companion


def connection(server="https://console.example.test", *, allow_local=False):
    return {"server":server,"device_id":str(uuid4()),"device_token":secrets.token_urlsafe(32),"allow_local":allow_local}


def config_file(tmp_path, data=None):
    path = tmp_path.resolve()/"private"/"connection.json"
    companion.write_new(path, connection() if data is None else data)
    return path


@pytest.mark.parametrize("url", ["https://console.example.test", "https://console.example.test/", "https://console.example.test:8443", "https://[::1]:8443"])
def test_https_exact_origin_is_allowed(url):
    assert companion.server_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", ["http://console.example.test", "http://127.0.0.1:8080", "ftp://console.example.test", "https://user:password@console.example.test", "https://console.example.test/path", "https://console.example.test?token=private", "https://console.example.test/#fragment", "//console.example.test", ""])
def test_non_https_non_origin_and_embedded_credentials_are_rejected(url):
    with pytest.raises(ValueError):
        companion.server_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080", "http://localhost:8080", "http://[::1]:8080"])
def test_local_http_requires_explicit_optin(url):
    assert companion.server_url(url,allow_local=True) == url


@pytest.mark.parametrize("url", ["http://192.168.1.3", "http://127.0.0.1.attacker.example", "http://2130706433", "http://localhost.attacker.example"])
def test_local_optin_does_not_allow_remote_or_ambiguous_hosts(url):
    with pytest.raises(ValueError):
        companion.server_url(url,allow_local=True)


@pytest.mark.parametrize("url", ["https://console.example.test:abc", "https://console.example.test:99999", "https://console.example.test:0", " https://console.example.test", "https://console.example.test\n", "https://console.\texample.test"])
def test_malformed_origin_is_rejected_before_request(url):
    with pytest.raises(ValueError):
        companion.server_url(url)


def test_private_credential_file_is_exclusive_and_owner_only(tmp_path):
    path = config_file(tmp_path)
    previous = path.read_bytes()
    with pytest.raises((FileExistsError,ValueError)):
        companion.write_new(path,connection())
    assert path.read_bytes() == previous
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert companion.load_connection(path)["device_token"] == json.loads(previous)["device_token"]


def test_existing_connection_stops_enrolment_before_prompt_or_network(tmp_path, monkeypatch, capsys):
    path = config_file(tmp_path)
    previous = path.read_bytes()
    monkeypatch.setattr(companion.getpass,"getpass",lambda *_:pytest.fail("Existing connection must not request another token"))
    monkeypatch.setattr(companion,"request",lambda *a,**k:pytest.fail("Existing connection must not contact server"))
    assert companion.main(["enrol","--server","https://console.example.test","--name","Synthetic laptop","--config",str(path)]) == 1
    assert path.read_bytes() == previous
    assert json.loads(previous)["device_token"] not in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt",reason="POSIX permission bits; Windows native ACL behavior needs platform test")
def test_world_readable_connection_is_rejected_without_chmod(tmp_path):
    path = config_file(tmp_path)
    path.chmod(0o644)
    previous = path.read_bytes()
    with pytest.raises(ValueError):
        companion.load_connection(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644 and path.read_bytes() == previous


@pytest.mark.skipif(os.name == "nt",reason="POSIX writable-directory permission contract")
def test_group_writable_credential_directory_is_rejected_and_preserved(tmp_path):
    directory = tmp_path.resolve()/"shared"
    directory.mkdir(mode=0o770)
    directory.chmod(0o770)
    with pytest.raises((ValueError,OSError)):
        companion.write_new(directory/"connection.json",connection())
    assert not (directory/"connection.json").exists()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o770


@pytest.mark.skipif(os.name == "nt",reason="Native Windows reparse cases are tested by its adapter suite")
def test_symbolic_file_or_parent_is_rejected(tmp_path):
    root=tmp_path.resolve()
    real=root/"real"
    real.mkdir()
    target=real/"connection.json"
    try:
        (root/"linked").symlink_to(real,target_is_directory=True)
        (root/"linked-file.json").symlink_to(target)
    except (OSError,NotImplementedError):
        pytest.skip("Symbolic links unavailable")
    for path in (root/"linked"/"connection.json",root/"linked-file.json"):
        with pytest.raises(ValueError):
            companion.write_new(path,connection())
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt",reason="Deterministic POSIX directory replacement race")
def test_directory_swap_cannot_redirect_credential_write(tmp_path,monkeypatch):
    root=tmp_path.resolve()
    selected,other=root/"selected",root/"other"
    selected.mkdir(mode=0o700)
    other.mkdir(mode=0o700)
    target=selected/"connection.json"
    original=os.open
    swapped=False
    def swap(path,*args,**kwargs):
        nonlocal swapped
        if path=="connection.json" and "dir_fd" in kwargs and not swapped:
            swapped=True
            selected.rename(root/"original")
            selected.symlink_to(other,target_is_directory=True)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(os,"open",swap)
    companion.write_new(target,connection())
    assert swapped, "The replacement must occur while a held parent descriptor is in use"
    assert not (other/"connection.json").exists(), "A replaced path must not redirect credentials"
    assert (root/"original"/"connection.json").exists()


@pytest.mark.parametrize("payload", [None,[],{"server":17,"device_id":"invalid","device_token":"x"*43,"allow_local":False},{"server":"https://console.example.test","device_id":"invalid","device_token":"x"*43,"allow_local":False},{"server":"https://console.example.test","device_id":str(uuid4()),"device_token":"x"*43,"allow_local":"false"}])
def test_malformed_private_config_fails_safely_without_network(tmp_path,monkeypatch,capsys,payload):
    path=tmp_path.resolve()/"connection.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    previous=path.read_bytes()
    monkeypatch.setattr(companion,"request",lambda *a,**k:pytest.fail("Invalid config must not reach network"))
    assert companion.main(["run","--once","--config",str(path)]) == 1
    assert path.read_bytes() == previous
    assert "Traceback" not in capsys.readouterr().out


@contextmanager
def local_server(handler):
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    server.daemon_threads=True
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        yield server,f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()


def test_http_redirect_never_forwards_device_credential():
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path,self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location",f"http://127.0.0.1:{self.server.server_port}/credential-target")
            self.end_headers()
        def do_GET(self):
            calls.append((self.path,self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
        def log_message(self,*args):
            pass
    with local_server(Handler) as (_,server):
        with pytest.raises(companion.ConnectionFailure):
            companion.request(server,"heartbeat",{},"synthetic-bearer-token")
    assert calls == [("/device-api/heartbeat","Bearer synthetic-bearer-token")]


def test_proxy_environment_is_not_used_and_body_is_bounded(monkeypatch):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x"*65537)
        def log_message(self,*args):
            pass
    with local_server(Handler) as (_,server):
        monkeypatch.setenv("HTTP_PROXY","http://127.0.0.1:1")
        monkeypatch.setenv("HTTPS_PROXY","http://127.0.0.1:1")
        monkeypatch.setenv("ALL_PROXY","http://127.0.0.1:1")
        with pytest.raises(companion.ConnectionFailure) as error:
            companion.request(server,"heartbeat",{},"synthetic-bearer-token")
        assert error.value.code == "INVALID_RESPONSE"
    assert calls == ["/device-api/heartbeat"]


def test_https_default_verification_rejects_untrusted_certificate(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"localhost")])
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(hours=1)).sign(key,hashes.SHA256()))
    key_file,cert_file=tmp_path/"synthetic.key",tmp_path/"synthetic.crt"
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    key_file.chmod(0o600)
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(True)
            self.send_response(200)
            self.end_headers()
        def log_message(self,*args):
            pass
    with local_server(Handler) as (server,_):
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file,key_file)
        server.socket=context.wrap_socket(server.socket,server_side=True)
        with pytest.raises(companion.ConnectionFailure):
            companion.request(f"https://127.0.0.1:{server.server_port}","heartbeat",{},"synthetic-bearer-token")
    assert calls == []


@pytest.mark.parametrize("payload", [None,[],{}, {"ok":False,"server_time":"2026-09-14T10:00:00Z"}, {"ok":1,"server_time":"2026-09-14T10:00:00Z"},{"ok":True,"server_time":"not-a-time"},{"ok":True,"server_time":"2026-09-14T10:00:00"}])
def test_false_or_malformed_success_is_not_reported_connected(tmp_path,monkeypatch,capsys,payload):
    path=config_file(tmp_path)
    monkeypatch.setattr(companion,"request",lambda *a,**k:payload)
    assert companion.main(["run","--once","--config",str(path)]) == 1
    assert "Connected." not in capsys.readouterr().out


@pytest.mark.parametrize("payload", [None,[],{}, {"device_id":"invalid","device_token":"x"*43,"heartbeat_interval_seconds":30},{"device_id":str(uuid4()),"device_token":"bad","heartbeat_interval_seconds":30},{"device_id":str(uuid4()),"device_token":"x"*43,"heartbeat_interval_seconds":True}])
def test_invalid_enrolment_response_never_creates_credentials(tmp_path,monkeypatch,capsys,payload):
    path=tmp_path.resolve()/"private"/"connection.json"
    monkeypatch.setattr(companion.getpass,"getpass",lambda *_:"synthetic-one-use-code")
    monkeypatch.setattr(companion,"request",lambda *a,**k:payload)
    assert companion.main(["enrol","--server","https://console.example.test","--name","Synthetic laptop","--config",str(path)]) == 1
    assert not path.exists()
    assert "Laptop registered" not in capsys.readouterr().out


def test_heartbeat_is_honest_unknown_and_never_reads_local_database(tmp_path,monkeypatch,capsys):
    path=config_file(tmp_path)
    private_db=path.parent/"aislesignals.db"
    private_db.write_bytes(b"synthetic-private-database-do-not-open")
    frames=path.parent/"private-frame.jpg"
    frames.write_bytes(b"synthetic-frame-do-not-open")
    calls=[]
    def send(server,route,body,token=None):
        calls.append((server,route,body,token))
        return {"ok":True,"server_time":datetime.now(timezone.utc).isoformat()}
    original=Path.read_bytes
    def guard_read(target):
        if target in {private_db,frames}:
            pytest.fail("Connection-only companion must not read footage or the local database")
        return original(target)
    monkeypatch.setattr(companion,"request",send)
    monkeypatch.setattr(Path,"read_bytes",guard_read)
    assert companion.main(["run","--once","--config",str(path)]) == 0
    assert len(calls)==1
    _,route,body,token=calls[0]
    assert route=="heartbeat" and body["monitoring_status"]=="UNKNOWN" and body["camera_count"]==0
    assert set(body)=={"sequence","monitoring_status","camera_count","app_version"}
    assert isinstance(body["sequence"],int) and body["sequence"]>0
    output=capsys.readouterr().out
    assert token not in output and "not reported" in output


def test_restart_after_clock_rollback_reserves_a_greater_sequence(tmp_path,monkeypatch):
    path=config_file(tmp_path)
    sequences=[]
    def send(server,route,body,token=None):
        sequences.append(body["sequence"])
        return {"ok":True,"server_time":"2026-09-14T10:00:00Z"}
    monkeypatch.setattr(companion,"request",send)
    monkeypatch.setattr(companion.time,"time",lambda:2000.0)
    assert companion.main(["run","--once","--config",str(path)])==0
    monkeypatch.setattr(companion.time,"time",lambda:1000.0)
    assert companion.main(["run","--once","--config",str(path)])==0
    assert sequences[1]>sequences[0]


def test_failed_request_still_persists_reserved_sequence(tmp_path,monkeypatch):
    path=config_file(tmp_path)
    sequences=[]
    def send(server,route,body,token=None):
        sequences.append(body["sequence"])
        if len(sequences)==1:
            raise companion.ConnectionFailure("SERVICE_UNAVAILABLE")
        return {"ok":True,"server_time":"2026-09-14T10:00:00Z"}
    monkeypatch.setattr(companion,"request",send)
    monkeypatch.setattr(companion.time,"time",lambda:1000.0)
    assert companion.main(["run","--once","--config",str(path)])==1
    assert companion.main(["run","--once","--config",str(path)])==0
    assert sequences[1]>sequences[0]


def test_revoked_access_stops_attended_loop_without_retry_or_secret_echo(tmp_path,monkeypatch,capsys):
    path=config_file(tmp_path)
    calls=[]
    def revoked(*args,**kwargs):
        calls.append(True)
        raise companion.ConnectionFailure("ACCESS_REVOKED")
    monkeypatch.setattr(companion,"request",revoked)
    monkeypatch.setattr(companion.time,"sleep",lambda *_:pytest.fail("Revoked credentials must not retry"))
    assert companion.main(["run","--config",str(path)])==1
    assert calls==[True]
    assert "revoked" in capsys.readouterr().out.lower()


@pytest.mark.skipif(os.name == "nt",reason="POSIX directory-descriptor adapter")
def test_symlink_swap_before_directory_open_is_rejected(tmp_path,monkeypatch):
    root=tmp_path.resolve()
    selected,other=root/"selected",root/"other"
    selected.mkdir(mode=0o700)
    other.mkdir(mode=0o700)
    original=os.open
    swapped=False
    def swap(path,*args,**kwargs):
        nonlocal swapped
        if path=="selected" and "dir_fd" in kwargs and not swapped:
            swapped=True
            selected.rename(root/"original")
            selected.symlink_to(other,target_is_directory=True)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(os,"open",swap)
    with pytest.raises((OSError,ValueError)):
        companion.write_new(selected/"connection.json",connection())
    assert swapped and not (other/"connection.json").exists()


@pytest.mark.skipif(os.name == "nt",reason="POSIX descriptor and advisory-lock adapter")
@pytest.mark.parametrize("raw", [b"",b"0",b"-1\n",b"1garbage\n",b"01\n",b"9007199254740992\n",b"1"*65])
def test_malformed_sequence_ledger_is_preserved_and_never_reset(tmp_path,raw):
    path=tmp_path.resolve()/"connection.json.sequence"
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        companion.reserve_sequence(path,now_ms=1000)
    assert path.read_bytes()==raw


@pytest.mark.skipif(os.name == "nt",reason="POSIX descriptor and advisory-lock adapter")
def test_sequence_lock_excludes_a_second_writer_without_changing_state(tmp_path):
    import fcntl
    path=tmp_path.resolve()/"connection.json.sequence"
    assert companion.reserve_sequence(path,now_ms=1000)==1000
    lock=path.with_name(path.name+".lock")
    with lock.open("r+b") as stream:
        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        previous=path.read_bytes()
        with pytest.raises(BlockingIOError):
            companion.reserve_sequence(path,now_ms=500)
        assert path.read_bytes()==previous
    assert companion.reserve_sequence(path,now_ms=500)==1001
    assert stat.S_IMODE(path.stat().st_mode)==0o600


@pytest.mark.skipif(os.name == "nt",reason="POSIX atomic replacement and fsync adapter")
def test_failed_counter_fsync_preserves_previous_ledger(tmp_path,monkeypatch):
    path=tmp_path.resolve()/"connection.json.sequence"
    assert companion.reserve_sequence(path,now_ms=1000)==1000
    previous=path.read_bytes()
    def fail(_):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(os,"fsync",fail)
    with pytest.raises(OSError):
        companion.reserve_sequence(path,now_ms=1001)
    assert path.read_bytes()==previous
    assert not list(path.parent.glob(".connection.json.sequence.*"))


@pytest.mark.skipif(os.name == "nt",reason="POSIX hard-link and regular-file validation")
def test_hard_linked_credential_is_not_read_or_modified(tmp_path):
    original=config_file(tmp_path)
    linked=original.with_name("linked.json")
    os.link(original,linked)
    previous=original.read_bytes()
    with pytest.raises(ValueError):
        companion.load_connection(linked)
    assert original.read_bytes()==previous


def test_missing_native_adapter_refuses_before_prompt_network_or_file_creation(tmp_path,monkeypatch,capsys):
    path=tmp_path/"untouched"/"connection.json"
    def unavailable():
        raise companion.PrivatePathUnsupported("The Windows storage adapter is missing.")
    monkeypatch.setattr(companion,"windows_storage",unavailable)
    monkeypatch.setattr(companion,"request",lambda *a,**k:pytest.fail("Unsupported storage must not contact server"))
    monkeypatch.setattr(companion.getpass,"getpass",lambda *_:pytest.fail("Unsupported storage must not request a code"))
    assert companion.main(["enrol","--server","https://console.example.test","--name","Synthetic Windows","--config",str(path)])==1
    assert not path.parent.exists()
    assert "Windows storage adapter" in capsys.readouterr().out
    with pytest.raises(companion.PrivatePathUnsupported):
        companion.reserve_sequence(path)


@pytest.mark.skipif(os.name == "nt",reason="POSIX private-file adapter")
def test_successful_enrolment_validates_and_writes_only_connection_metadata(tmp_path,monkeypatch,capsys):
    target=tmp_path.resolve()/"private"/"connection.json"
    credential=secrets.token_urlsafe(32)
    identifier=str(uuid4())
    calls=[]
    monkeypatch.setattr(companion.getpass,"getpass",lambda *_:"synthetic-one-use-code")
    def enrol(server,route,body,token=None):
        calls.append((server,route,body,token))
        return {"device_id":identifier,"device_token":credential,"heartbeat_interval_seconds":30}
    monkeypatch.setattr(companion,"request",enrol)
    assert companion.main(["enrol","--server","https://console.example.test","--name","Synthetic laptop","--config",str(target)])==0
    data=companion.load_connection(target)
    assert data=={"server":"https://console.example.test","device_id":identifier,"device_token":credential,"allow_local":False}
    assert calls[0][1]=="enrol" and calls[0][3] is None
    assert set(calls[0][2])=={"token","name","platform","app_version"}
    output=capsys.readouterr().out
    assert credential not in output and "synthetic-one-use-code" not in output


@pytest.mark.skipif(os.name == "nt",reason="POSIX exclusive-file creation")
def test_concurrent_existing_file_is_preserved_after_server_reply(tmp_path,monkeypatch):
    target=tmp_path.resolve()/"private"/"connection.json"
    monkeypatch.setattr(companion.getpass,"getpass",lambda *_:"synthetic-one-use-code")
    def competing_file(*args,**kwargs):
        target.write_bytes(b"synthetic-existing-file-to-preserve")
        target.chmod(0o600)
        return {"device_id":str(uuid4()),"device_token":secrets.token_urlsafe(32),"heartbeat_interval_seconds":30}
    monkeypatch.setattr(companion,"request",competing_file)
    assert companion.main(["enrol","--server","https://console.example.test","--name","Synthetic laptop","--config",str(target)])==1
    assert target.read_bytes()==b"synthetic-existing-file-to-preserve"


@pytest.mark.skipif(os.name == "nt",reason="POSIX preflight must refuse unsafe parents before network")
def test_unsafe_directory_rejected_before_one_use_code_is_consumed(tmp_path,monkeypatch):
    directory=tmp_path.resolve()/"shared"
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    monkeypatch.setattr(companion.getpass,"getpass",lambda *_:pytest.fail("Unsafe destination must fail before code prompt"))
    monkeypatch.setattr(companion,"request",lambda *a,**k:pytest.fail("Unsafe destination must not consume code"))
    assert companion.main(["enrol","--server","https://console.example.test","--name","Synthetic laptop","--config",str(directory/"connection.json")])==1
    assert not (directory/"connection.json").exists()
    assert stat.S_IMODE(directory.stat().st_mode)==0o777


@pytest.mark.skipif(os.name == "nt",reason="POSIX durable sequence adapter")
def test_sequence_reservation_survives_actual_new_process_with_earlier_clock(tmp_path):
    import subprocess
    import sys
    path=tmp_path.resolve()/"connection.json.sequence"
    code="import runpy,sys; from pathlib import Path; ns=runpy.run_path(sys.argv[1]); print(ns['reserve_sequence'](Path(sys.argv[2]),now_ms=int(sys.argv[3])))"
    script=str(Path(companion.__file__).resolve())
    first=subprocess.run([sys.executable,"-c",code,script,str(path),"2000"],capture_output=True,text=True,check=True,timeout=10)
    second=subprocess.run([sys.executable,"-c",code,script,str(path),"1000"],capture_output=True,text=True,check=True,timeout=10)
    assert first.stdout.strip()=="2000" and second.stdout.strip()=="2001"


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows storage integration; synthetic network only")
def test_native_windows_enrolment_then_heartbeat_preserves_private_connection(tmp_path,monkeypatch,capsys):
    target=tmp_path/"private"/"connection.json"
    credential=secrets.token_urlsafe(32)
    identifier=str(uuid4())
    calls=[]
    monkeypatch.setattr(companion.getpass,"getpass",lambda *_:"synthetic-one-use-code")
    def send(server,route,body,token=None):
        calls.append((route,body,token))
        if route=="enrol":
            assert body["platform"]=="WINDOWS"
            return {"device_id":identifier,"device_token":credential,"heartbeat_interval_seconds":30}
        assert route=="heartbeat" and token==credential
        assert body["monitoring_status"]=="UNKNOWN" and body["camera_count"]==0
        return {"ok":True,"server_time":"2026-09-14T10:00:00Z"}
    monkeypatch.setattr(companion,"request",send)
    assert companion.main(["enrol","--server","https://console.example.test","--name","Synthetic Windows","--config",str(target)])==0
    before=target.read_bytes()
    assert companion.main(["run","--once","--config",str(target)])==0
    assert companion.load_connection(target)["device_id"]==identifier
    assert target.read_bytes()==before and [call[0] for call in calls]==["enrol","heartbeat"]
    output=capsys.readouterr().out
    assert credential not in output and "synthetic-one-use-code" not in output


def test_native_preflight_refusal_cannot_consume_connection_code(tmp_path,monkeypatch):
    from types import SimpleNamespace
    target=tmp_path/"connection.json"
    def reject(path):
        raise ValueError("Synthetic unsafe Windows directory")
    native=SimpleNamespace(validate_path=lambda path:Path(path),ensure_new_destination=reject)
    monkeypatch.setattr(companion,"windows_storage",lambda:native)
    monkeypatch.setattr(companion.getpass,"getpass",lambda *_:pytest.fail("Unsafe native destination requested a code"))
    monkeypatch.setattr(companion,"request",lambda *a,**k:pytest.fail("Unsafe native destination contacted server"))
    assert companion.main(["enrol","--server","https://console.example.test","--name","Synthetic Windows","--config",str(target)])==1
    assert not target.exists()
