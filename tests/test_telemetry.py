from __future__ import annotations

import gzip
import json
import os
import queue
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
import sentry_sdk
from sentry_sdk.consts import EndpointType

from blue_skies.telemetry import BoundedTransport, initialize


@pytest.fixture(scope="module")
def certificate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    directory = tmp_path_factory.mktemp("collector-ca")
    certificate = directory / "certificate.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        capture_output=True,
        check=True,
    )
    return certificate, key


class Collector(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, certificate: tuple[Path, Path], status: int | str | None) -> None:
        self.envelopes: queue.Queue[bytes] = queue.Queue()
        self.stop_response = threading.Event()
        self.response_status = status
        super().__init__(("127.0.0.1", 0), CollectorHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*certificate)
        self.socket = context.wrap_socket(self.socket, server_side=True)


class CollectorHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        server = cast("Collector", self.server)
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        server.envelopes.put(body)
        if server.response_status == "complete":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if server.response_status == "drip-headers":
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Drip: ")
                self.wfile.flush()
                while not server.stop_response.wait(0.3):
                    self.wfile.write(b"x")
                    self.wfile.flush()
            except OSError:
                pass
            return
        if server.response_status is not None:
            self.send_response(
                200 if isinstance(server.response_status, str) else server.response_status
            )
            self.send_header("Content-Length", "1000000")
            self.send_header("Location", "http://127.0.0.1:1/plaintext-secret-leak")
            self.end_headers()
            self.wfile.flush()
        if server.response_status == "drip-body":
            try:
                while not server.stop_response.wait(0.3):
                    self.wfile.write(b"x")
                    self.wfile.flush()
            except OSError:
                pass
            return
        # Deliberately never complete a declared response body, or any headers.
        server.stop_response.wait(8)


def test_unconfigured_reporting_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLUE_SKIES_SENTRY_DSN", raising=False)
    client = Mock()
    monkeypatch.setattr("blue_skies.telemetry.sentry_sdk.init", client)
    initialize()
    client.assert_not_called()


@pytest.mark.parametrize("dsn", ["http://public@localhost/1", "file:///private/photo"])
def test_plaintext_collectors_are_rejected(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    monkeypatch.setenv("BLUE_SKIES_SENTRY_DSN", dsn)
    with pytest.raises(ValueError, match="verified HTTPS"):
        initialize()


def test_release_must_be_full_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLUE_SKIES_SENTRY_DSN", "https://public@localhost/1")
    monkeypatch.setenv("SENTRY_RELEASE", "dev")
    with pytest.raises(ValueError, match="full Git commit hash"):
        initialize()


@pytest.mark.parametrize("status", [None, 200, 307, 500, "drip-headers", "drip-body"])
def test_cli_capture_and_shutdown_with_stalled_collector(
    certificate: tuple[Path, Path], status: int | str | None, tmp_path: Path
) -> None:
    server = Collector(certificate, status)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    release = "a" * 40
    env = {
        **os.environ,
        "BLUE_SKIES_SENTRY_DSN": f"https://public@localhost:{server.server_port}/1",
        "SENTRY_RELEASE": release,
        "SSL_CERT_FILE": str(certificate[0]),
        # This cannot disable native certificate verification.
        "SSL_VERIFY": "false",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
    }
    start = time.monotonic()
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "blue_skies.cli",
                "render",
                "--manifest",
                str(tmp_path / "secret-photo-manifest.csv"),
                "--source-dir",
                str(tmp_path / "private-photographs"),
                "--output-dir",
                str(tmp_path / "output"),
            ],
            env=env,
            capture_output=True,
            timeout=6,
        )
        assert result.returncode != 0
        assert time.monotonic() - start < 5
        envelope = server.envelopes.get(timeout=1)
        event = json.loads(envelope.splitlines()[2])
        assert event["release"] == f"blue-skies@{release}"
        assert event["exception"]["values"][-1]["type"] == "FileNotFoundError"
        assert "secret-photo" not in envelope.decode()
        assert "private-photographs" not in envelope.decode()
        assert str(tmp_path) not in envelope.decode()
        assert not (tmp_path / "output").exists()
        assert set(event) <= {
            "event_id",
            "timestamp",
            "level",
            "platform",
            "release",
            "environment",
            "exception",
        }
    finally:
        server.stop_response.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_collector_certificates_must_be_verified(certificate: tuple[Path, Path]) -> None:
    server = Collector(certificate, 200)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        **os.environ,
        "BLUE_SKIES_SENTRY_DSN": f"https://public@localhost:{server.server_port}/1",
        "SENTRY_RELEASE": "a" * 40,
        "SSL_VERIFY": "false",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
    }
    env.pop("SSL_CERT_FILE", None)
    env.pop("REQUESTS_CA_BUNDLE", None)
    try:
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from blue_skies.telemetry import initialize; initialize(); "
                "raise RuntimeError('private-photo-data')",
            ],
            env=env,
            capture_output=True,
            timeout=6,
        )
        assert server.envelopes.empty()
    finally:
        server.stop_response.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("mode", ["drip-headers", "drip-body"])
def test_http_deadline_frees_transport_for_the_next_upload(
    certificate: tuple[Path, Path], mode: str
) -> None:
    server = Collector(certificate, mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = sentry_sdk.Client(
        dsn=f"https://public@localhost:{server.server_port}/1",
        ca_certs=str(certificate[0]),
        default_integrations=False,
        transport=BoundedTransport,
    )
    transport = client.transport
    assert isinstance(transport, BoundedTransport)
    try:
        start = time.monotonic()
        with pytest.raises(subprocess.CalledProcessError):
            transport._request("POST", EndpointType.ENVELOPE, b"first envelope", {})
        assert time.monotonic() - start < 3.5
        assert server.envelopes.get(timeout=1) == b"first envelope"
        server.response_status = "complete"
        response = transport._request("POST", EndpointType.ENVELOPE, b"second envelope", {})
        assert response.status == 200
        assert server.envelopes.get(timeout=1) == b"second envelope"
    finally:
        client.close()
        server.stop_response.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
