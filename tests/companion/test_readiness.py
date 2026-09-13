import contextlib
import io
import json
from types import SimpleNamespace
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

from services.companion.__main__ import main
from services.companion.readiness import (
    MAX_RESPONSE_BYTES, MIN_FREE_BYTES, ProbeError,
    parse_target, preflight, probe_environment, socket_transport,
)


URL = "rtsp://192.168.12.30:554/Streaming/Channels/101"
SECRET = "secret-camera-password-never-emit"
VIDEO = b"v=0\r\ns=private camera\r\nm=video 0 RTP/AVP 96\r\na=control:rtsp://203.0.113.1/ignored\r\n"


def response(body=VIDEO, *, code=200, headers=None):
    selected = {"CSeq": "1", "Content-Type": "application/sdp", "Content-Length": str(len(body))}
    selected.update(headers or {})
    return (f"RTSP/1.0 {code} Result\r\n" + "".join(f"{k}: {v}\r\n" for k, v in selected.items()) + "\r\n").encode() + body


def probe(url=URL, payload=None, **kwargs):
    return probe_environment("CAMERA_ENDPOINT", True, environment={"CAMERA_ENDPOINT": url},
                             transport=Mock(return_value=response() if payload is None else payload), **kwargs)


def ready(**kwargs):
    defaults = {"disk_usage": lambda _: SimpleNamespace(free=MIN_FREE_BYTES + 1),
                "which": lambda _: "/private/local/ffprobe", "system": lambda: "Darwin",
                "machine": lambda: "arm64", "python_version": (3, 11, 1)}
    defaults.update(kwargs)
    return preflight(**defaults)


class PreflightTests(unittest.TestCase):
    def test_mac_inventory_is_not_a_readiness_promise(self):
        report = ready()
        self.assertEqual(report["platform"]["family"], "MACOS")
        self.assertEqual(report["status"], "CHECKS_COMPLETE")
        self.assertFalse(report["production_ready"])
        self.assertFalse(report["monitoring_active"])
        self.assertEqual(report["network_probe"]["status"], "NOT_REQUESTED")

    def test_windows_is_required_and_recognised(self):
        report = ready(system=lambda: "Windows", machine=lambda: "AMD64")
        self.assertEqual(report["platform"]["family"], "WINDOWS")
        self.assertEqual(report["platform"]["architecture"], "amd64")

    def test_unsupported_platform_is_blocked(self):
        self.assertEqual(ready(system=lambda: "Linux")["status"], "BLOCKED")

    def test_old_python_is_blocked(self):
        self.assertEqual(ready(python_version=(3, 10, 14))["status"], "BLOCKED")

    def test_low_disk_and_boundary(self):
        self.assertEqual(ready(disk_usage=lambda _: SimpleNamespace(free=MIN_FREE_BYTES - 1))["status"], "BLOCKED")
        self.assertEqual(ready(disk_usage=lambda _: SimpleNamespace(free=MIN_FREE_BYTES))["status"], "CHECKS_COMPLETE")

    def test_disk_error_is_redacted(self):
        report = ready(disk_usage=Mock(side_effect=OSError(SECRET)))
        self.assertEqual(report["status"], "BLOCKED")
        self.assertNotIn(SECRET, json.dumps(report))

    def test_disk_path_and_binary_path_are_not_reported(self):
        report = ready(disk_path="/Users/private-person/documents")
        self.assertNotIn("/Users", json.dumps(report))
        self.assertNotIn("/private/local", json.dumps(report))

    def test_missing_ffprobe_warns_without_installation(self):
        report = ready(which=lambda _: None)
        self.assertFalse(report["ffprobe_available"])
        self.assertEqual(report["status"], "CHECKS_COMPLETE")
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "ffprobe")["status"], "WARNING")


class InputTests(unittest.TestCase):
    def test_authorisation_precedes_environment_access(self):
        environment = Mock()
        result = probe_environment("CAMERA_ENDPOINT", False, environment=environment)
        self.assertEqual(result["code"], "AUTHORISATION_REQUIRED")
        environment.get.assert_not_called()

    def test_url_is_not_an_environment_name(self):
        result = probe_environment(f"rtsp://user:{SECRET}@192.168.1.2/live", True)
        self.assertEqual(result["code"], "INVALID_ENV_NAME")
        self.assertNotIn(SECRET, json.dumps(result))

    def test_missing_environment_variable(self):
        self.assertEqual(probe_environment("CAMERA_ENDPOINT", True, environment={})["code"], "URL_NOT_SET")

    def test_supported_private_literal_targets(self):
        for url in [URL, "rtsp://10.2.3.4:8554/live", "rtsps://172.16.1.2/live", "rtsp://[fd12:abcd::1]/live"]:
            with self.subTest(url=url):
                self.assertIsNotNone(parse_target(url))

    def test_credentials_never_reach_transport_or_report(self):
        for url in [f"rtsp://user:{SECRET}@192.168.1.2/live", f"rtsp://192.168.1.2/live?token={SECRET}", "rtsp://user@192.168.1.2/live"]:
            transport = Mock()
            result = probe_environment("CAMERA_ENDPOINT", True, environment={"CAMERA_ENDPOINT": url}, transport=transport)
            self.assertEqual(result["code"], "CREDENTIALS_UNSUPPORTED")
            self.assertNotIn(SECRET, json.dumps(result))
            transport.assert_not_called()

    def test_external_loopback_linklocal_and_dns_are_rejected(self):
        for host in ["8.8.8.8", "127.0.0.1", "169.254.169.254", "[::1]", "[fe80::1]", "camera.local", "example.com", "100.64.1.1", "192.168.1.2.example.com", "2130706433"]:
            with self.subTest(host=host):
                self.assertEqual(probe(f"rtsp://{host}/live")["code"], "PRIVATE_IP_REQUIRED")

    def test_non_rtsp_schemes_are_rejected(self):
        for url in ["http://192.168.1.2/live", "file:///private/footage.mp4", "concat:rtsp://192.168.1.2/live", "rtsp+tcp://192.168.1.2/live"]:
            with self.subTest(url=url):
                self.assertEqual(probe(url)["code"], "UNSUPPORTED_SCHEME")

    def test_path_injection_traversal_and_playlists_are_rejected(self):
        for path in ["/../etc", "/a/./b", "/a/%2e%2e/b", "/a/%252e/b", "/live.m3u8", "/live.PLS", "/live\\secret", "/live;rm", "/live\r\nAuthorization:secret", "/a#fragment"]:
            with self.subTest(path=path):
                self.assertEqual(probe("rtsp://192.168.1.2" + path)["status"], "BLOCKED")

    def test_invalid_or_sensitive_service_ports(self):
        for port in ["0", "22", "80", "443", "65536", "-1", "word"]:
            with self.subTest(port=port):
                self.assertEqual(probe(f"rtsp://192.168.1.2:{port}/live")["status"], "BLOCKED")

    def test_ambiguous_authorities_are_rejected(self):
        for authority in ["[fd00::1]extra", "192.168.1.2:", "[fd00::1]:", "[fd00::1]192.168.1.2"]:
            with self.subTest(authority=authority):
                self.assertEqual(probe(f"rtsp://{authority}/live")["status"], "BLOCKED")

    def test_long_control_and_non_ascii_urls(self):
        for url in ["", URL + "x" * 2048, URL + "é", " " + URL, URL + "\x00"]:
            with self.subTest(length=len(url)):
                self.assertEqual(probe(url)["status"], "BLOCKED")

    def test_target_repr_does_not_contain_address_or_path(self):
        self.assertNotIn("192.168", repr(parse_target(URL)))
        self.assertNotIn("Channels", repr(parse_target(URL)))


class ResponseTests(unittest.TestCase):
    def test_success_is_metadata_only_and_sanitised(self):
        result = probe()
        self.assertEqual(result["status"], "METADATA_REACHABLE")
        self.assertTrue(result["video_described"])
        self.assertFalse(result["recorded"])
        self.assertEqual(result["frames_read"], 0)
        self.assertNotIn("192.168", json.dumps(result))
        self.assertNotIn("private camera", json.dumps(result))
        self.assertNotIn("203.0.113", json.dumps(result))

    def test_redirect_is_not_followed(self):
        transport = Mock(return_value=response(code=302, headers={"Location": f"rtsp://user:{SECRET}@8.8.8.8/live"}))
        result = probe_environment("CAMERA_ENDPOINT", True, environment={"CAMERA_ENDPOINT": URL}, transport=transport)
        self.assertEqual(result["code"], "REDIRECT_REJECTED")
        self.assertEqual(transport.call_count, 1)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_authentication_required_is_not_worked_around(self):
        for code in [401, 403]:
            self.assertEqual(probe(payload=response(code=code))["code"], "AUTHENTICATION_REQUIRED")

    def test_server_rejection_is_not_success(self):
        self.assertEqual(probe(payload=response(code=500))["code"], "RTSP_REJECTED")

    def test_no_video(self):
        self.assertEqual(probe(payload=response(b"v=0\r\nm=audio 0 RTP/AVP 0\r\n"))["code"], "NO_VIDEO_DESCRIPTION")

    def test_truncated_and_wrong_content_type(self):
        self.assertEqual(probe(payload=response()[:-2])["code"], "INVALID_RESPONSE")
        self.assertEqual(probe(payload=response(headers={"Content-Type": "text/html"}))["code"], "INVALID_RESPONSE")

    def test_oversized_response_and_length(self):
        self.assertEqual(probe(payload=b"x" * (MAX_RESPONSE_BYTES + 1))["code"], "RESPONSE_TOO_LARGE")
        self.assertEqual(probe(payload=response(headers={"Content-Length": "999999"}))["code"], "RESPONSE_TOO_LARGE")

    def test_malformed_headers_do_not_expose_body(self):
        for payload in [response(headers={"Content-Length": "-1"}), response(headers={"CSeq": "2"}), b"HTTP/1.0 200 OK\r\n\r\n" + SECRET.encode(), response().replace(b"CSeq: 1", b"CSeq: 1\r\nCSeq: 1"), b"RTSP/1.0 200 OK\r\nX: \xff\r\n\r\n"]:
            with self.subTest(payload_length=len(payload)):
                result = probe(payload=payload)
                self.assertEqual(result["code"], "INVALID_RESPONSE")
                self.assertNotIn(SECRET, json.dumps(result))

    def test_timeout_and_unexpected_exception_are_redacted(self):
        for error, code in [(TimeoutError(SECRET), "PROBE_TIMEOUT"), (RuntimeError(SECRET + URL), "UNEXPECTED_FAILURE")]:
            result = probe_environment("CAMERA_ENDPOINT", True, environment={"CAMERA_ENDPOINT": URL}, transport=Mock(side_effect=error))
            self.assertEqual(result["code"], code)
            self.assertNotIn(SECRET, json.dumps(result))
            self.assertNotIn(URL, json.dumps(result))


class SocketTransportTests(unittest.TestCase):
    def test_single_describe_no_dns_play_or_followup(self):
        connection = Mock()
        connection.recv.side_effect = [response()[:32], response()[32:]]
        with patch("services.companion.readiness.socket.socket", return_value=connection) as make_socket, patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")):
            received = socket_transport(parse_target(URL), 8.0, MAX_RESPONSE_BYTES)
        self.assertEqual(received, response())
        make_socket.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        connection.connect.assert_called_once_with(("192.168.12.30", 554))
        request = connection.sendall.call_args.args[0]
        self.assertTrue(request.startswith(b"DESCRIBE "))
        self.assertNotIn(b"PLAY", request)
        self.assertNotIn(b"Authorization", request)
        connection.sendall.assert_called_once()
        connection.close.assert_called_once()

    def test_socket_timeout_closes_and_redacts(self):
        connection = Mock()
        connection.connect.side_effect = socket.timeout(SECRET)
        with patch("services.companion.readiness.socket.socket", return_value=connection):
            with self.assertRaises(ProbeError) as caught:
                socket_transport(parse_target(URL), 8.0, MAX_RESPONSE_BYTES)
        self.assertEqual(caught.exception.code, "PROBE_TIMEOUT")
        self.assertNotIn(SECRET, str(caught.exception))
        connection.close.assert_called_once()

    def test_connection_failure_closes(self):
        connection = Mock()
        connection.connect.side_effect = OSError(SECRET)
        with patch("services.companion.readiness.socket.socket", return_value=connection):
            with self.assertRaises(ProbeError) as caught:
                socket_transport(parse_target(URL), 8.0, MAX_RESPONSE_BYTES)
        self.assertEqual(caught.exception.code, "CONNECTION_FAILED")
        connection.close.assert_called_once()

    def test_oversized_headers_stop_reading(self):
        connection = Mock()
        connection.recv.side_effect = [b"x" * 4096, b"x" * 4096, b"x" * 4096]
        with patch("services.companion.readiness.socket.socket", return_value=connection):
            with self.assertRaises(ProbeError) as caught:
                socket_transport(parse_target(URL), 8.0, MAX_RESPONSE_BYTES)
        self.assertEqual(caught.exception.code, "RESPONSE_TOO_LARGE")
        self.assertEqual(connection.recv.call_count, 3)
        connection.close.assert_called_once()

    def test_tls_verification_is_not_disabled(self):
        connection = Mock()
        context = Mock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError(SECRET)
        with patch("services.companion.readiness.socket.socket", return_value=connection), patch("services.companion.readiness.ssl.create_default_context", return_value=context):
            with self.assertRaises(ProbeError) as caught:
                socket_transport(parse_target("rtsps://192.168.1.2/live"), 8.0, MAX_RESPONSE_BYTES)
        self.assertEqual(caught.exception.code, "TLS_VERIFICATION_FAILED")
        context.wrap_socket.assert_called_once_with(connection, server_hostname="192.168.1.2")
        connection.close.assert_called_once()

    def test_total_deadline_expires_even_before_socket_timeout(self):
        connection = Mock()
        with patch("services.companion.readiness.socket.socket", return_value=connection), patch("services.companion.readiness.time.monotonic", side_effect=[0.0, 0.1, 0.2, 8.1]):
            with self.assertRaises(ProbeError) as caught:
                socket_transport(parse_target(URL), 8.0, MAX_RESPONSE_BYTES)
        self.assertEqual(caught.exception.code, "PROBE_TIMEOUT")
        connection.recv.assert_not_called()
        connection.close.assert_called_once()


class CliTests(unittest.TestCase):
    def test_default_command_does_not_probe(self):
        output = io.StringIO()
        with patch("services.companion.__main__.preflight", return_value=ready()), patch("services.companion.__main__.probe_environment") as network, contextlib.redirect_stdout(output):
            self.assertEqual(main([]), 0)
        network.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["network_probe"]["status"], "NOT_REQUESTED")

    def test_low_disk_stops_requested_probe(self):
        report = ready(disk_usage=lambda _: SimpleNamespace(free=1))
        output = io.StringIO()
        with patch("services.companion.__main__.preflight", return_value=report), patch("services.companion.__main__.probe_environment") as network, contextlib.redirect_stdout(output):
            self.assertEqual(main(["--probe-env", "CAMERA_ENDPOINT", "--authorised"]), 2)
        network.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["network_probe"]["code"], "READINESS_BLOCKED")

    def test_invalid_cli_does_not_echo_secret_argument(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as caught:
            main([f"rtsp://user:{SECRET}@192.168.1.2/live"])
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn(SECRET, errors.getvalue())
        self.assertEqual(json.loads(errors.getvalue())["code"], "INVALID_ARGUMENTS")

    def test_cli_unexpected_failure_does_not_echo_secrets(self):
        output = io.StringIO()
        with patch("services.companion.__main__.preflight", side_effect=RuntimeError(SECRET)), contextlib.redirect_stdout(output):
            self.assertEqual(main([]), 2)
        self.assertNotIn(SECRET, output.getvalue())


if __name__ == "__main__":
    unittest.main()
