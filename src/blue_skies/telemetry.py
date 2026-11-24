"""Optional exception reporting without photographs, paths, arguments or messages."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from email.parser import BytesHeaderParser
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import sentry_sdk
import urllib3
from sentry_sdk.consts import EndpointType
from sentry_sdk.integrations.atexit import AtexitIntegration
from sentry_sdk.integrations.excepthook import ExcepthookIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import HttpTransport

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint


class BoundedTransport(HttpTransport):
    """Keep native SDK queuing/rate limits; curl bounds every HTTP phase."""

    def _request(
        self, method: str, endpoint_type: EndpointType, body: bytes, headers: Mapping[str, str]
    ) -> urllib3.HTTPResponse:
        from select import PIPE_BUF

        header_data = "".join(f"{name}: {value}\n" for name, value in headers.items()).encode()
        if len(header_data) > PIPE_BUF:
            raise ValueError("Sentry upload headers are too large")
        reader, writer = os.pipe()
        try:
            with os.fdopen(writer, "wb") as stream:
                stream.write(header_data)
            response = subprocess.run(
                [
                    "curl",
                    "--disable",
                    "--silent",
                    "--globoff",
                    "--proto",
                    "=https",
                    "--noproxy",
                    "*",
                    "--connect-timeout",
                    "1",
                    "--max-time",
                    "2",
                    "--no-location",
                    "--max-redirs",
                    "0",
                    "--retry",
                    "0",
                    "--cacert",
                    self._get_pool_options()["ca_certs"],
                    "--header",
                    f"@/dev/fd/{reader}",
                    "--data-binary",
                    "@-",
                    "--output",
                    os.devnull,
                    "--dump-header",
                    "-",
                    "--write-out",
                    "%{http_code}",
                    "--url",
                    self._auth.get_api_url(endpoint_type),
                ],
                input=body,
                capture_output=True,
                pass_fds=(reader,),
                timeout=3,
                check=True,
            ).stdout
            block, _, status = response.rpartition(b"\r\n\r\n")
            parsed = BytesHeaderParser().parsebytes(block.partition(b"\r\n")[2])
            return urllib3.HTTPResponse(
                status=int(status), headers=urllib3.HTTPHeaderDict(parsed.items())
            )
        finally:
            os.close(reader)


def _safe_event(event: Event, hint: Hint) -> Event:
    hint["attachments"] = []
    exceptions = []
    for exception in event.get("exception", {}).get("values", []):
        frames = [
            {key: frame[key] for key in ("module", "function", "lineno", "in_app") if key in frame}
            for frame in exception.get("stacktrace", {}).get("frames", [])
        ]
        exceptions.append(
            {"type": exception.get("type", "Error"), "stacktrace": {"frames": frames}}
        )
    safe = {
        **{
            key: event[key]
            for key in ("event_id", "timestamp", "level", "platform", "release", "environment")
            if key in event
        },
        "exception": {"values": exceptions},
    }
    if "logentry" in event:
        safe["message"] = "Logged error"
    return cast("Event", safe)


def initialize() -> None:
    """Enable reporting only when explicitly configured with a verified HTTPS DSN."""
    dsn = os.environ.get("BLUE_SKIES_SENTRY_DSN")
    if not dsn:
        return
    if urlsplit(dsn).scheme != "https":
        raise ValueError("BLUE_SKIES_SENTRY_DSN must use verified HTTPS")
    revision = os.environ.get("SENTRY_RELEASE")
    if not revision:
        module = Path(__file__).resolve()
        source = module.parents[2]
        if source / "src" / "blue_skies" / "telemetry.py" != module:
            raise ValueError("Installed packages require an explicit SENTRY_RELEASE")
        metadata = tomllib.loads((source / "pyproject.toml").read_text())
        if metadata.get("project", {}).get("name") != "blue-skies":
            raise ValueError("SENTRY_RELEASE requires a Blue Skies source checkout")
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "ls-files",
                "--error-unmatch",
                "--",
                "pyproject.toml",
                "src/blue_skies/telemetry.py",
            ],
            capture_output=True,
            timeout=1,
            check=False,
        )
        if tracked.returncode:
            raise ValueError("Source archives require an explicit SENTRY_RELEASE")
        result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1,
            check=True,
        )
        revision = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("SENTRY_RELEASE must be a full Git commit hash")
    sentry_sdk.init(
        dsn=dsn,
        release=f"blue-skies@{revision}",
        environment="production",
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[
            AtexitIntegration(),
            ExcepthookIntegration(),
            LoggingIntegration(level=None, event_level=logging.ERROR),
        ],
        transport=BoundedTransport,
        shutdown_timeout=2,
        send_default_pii=False,
        include_local_variables=False,
        max_breadcrumbs=0,
        send_client_reports=False,
        enable_tracing=False,
        before_send=_safe_event,
    )
