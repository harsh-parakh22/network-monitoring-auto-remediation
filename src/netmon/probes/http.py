"""HTTP service health probe.

Uses the stdlib (urllib in a worker thread) rather than pulling in an
HTTP client dependency: one GET with a timeout needs no connection
pooling or async HTTP framework. The probe validates the status code and
optionally the response body, so "200 but wrong content" is detectable —
a real-world failure mode where a reverse proxy answers on behalf of a
dead application.
"""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.request

from netmon.config.models import TargetConfig
from netmon.probes.base import Probe, ProbeResult, ProbeStatus, ProbeType


class HTTPProbe(Probe):
    probe_type = ProbeType.HTTP

    def __init__(self, target: TargetConfig, port: int | None = None) -> None:
        super().__init__(target)
        self.port = port if port is not None else target.port
        self.url = f"http://{self.target.host}:{self.port}{self.target.http_path}"

    async def check(self) -> ProbeResult:
        start = time.monotonic()
        try:
            status, body = await asyncio.wait_for(
                asyncio.to_thread(self._fetch_blocking),
                timeout=self.target.timeout_seconds,
            )
        except TimeoutError:
            return ProbeResult(
                target=self.target.name,
                probe=self.probe_type,
                status=ProbeStatus.TIMEOUT,
                latency_ms=round((time.monotonic() - start) * 1000, 3),
                error="http request timed out",
                error_class="timeout",
            )
        except urllib.error.HTTPError as e:
            # Server answered with an error status — that IS the measurement.
            return self._result(start, status=e.code, body="")
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            return ProbeResult(
                target=self.target.name,
                probe=self.probe_type,
                status=ProbeStatus.FAILED,
                latency_ms=round((time.monotonic() - start) * 1000, 3),
                error=str(reason),
                error_class="connection",
            )
        else:
            return self._result(start, status=status, body=body)

    def _result(self, start: float, status: int, body: str) -> ProbeResult:
        ok = status == self.target.http_expected_status
        if ok and self.target.http_expected_body is not None:
            ok = self.target.http_expected_body in body
        latency_ms = round((time.monotonic() - start) * 1000, 3)
        if ok:
            return ProbeResult(
                target=self.target.name,
                probe=self.probe_type,
                status=ProbeStatus.OK,
                latency_ms=latency_ms,
                http_status=status,
            )
        return ProbeResult(
            target=self.target.name,
            probe=self.probe_type,
            status=ProbeStatus.FAILED,
            latency_ms=latency_ms,
            error=(
                f"unexpected status {status}"
                if status != self.target.http_expected_status
                else "body mismatch"
            ),
            error_class=f"http_{status}",
            http_status=status,
        )

    def _fetch_blocking(self) -> tuple[int, str]:
        # Scheme hardcoded to http:// and host/path come from pydantic-
        # validated config: no user-controlled URL reaches this call.
        # (Bandit B310 suppressed centrally in pyproject [tool.bandit].)
        req = urllib.request.Request(self.url, method="GET", headers={"User-Agent": "netmon/0.1"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=self.target.timeout_seconds) as resp:  # noqa: S310
            return resp.status, resp.read(4096).decode("utf-8", errors="replace")
