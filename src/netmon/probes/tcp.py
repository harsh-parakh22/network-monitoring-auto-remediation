"""TCP connectivity probe.

A TCP connect measures the SYN -> SYN/ACK -> ACK handshake: success means
the host is reachable *and* a process is listening on the port. The
failure mode is diagnostically valuable and is preserved in error_class:

- ConnectionRefusedError  -> "refused"   (RST: host up, port closed)
- TimeoutError / OSError   -> "timeout"  (packets silently dropped:
                                          host down or firewall DROP)

Distinguishing these two is the core of telling "service crashed" apart
from "network down", which is why they are separate classes rather than
a single boolean.
"""

from __future__ import annotations

import asyncio
import socket
import time

from netmon.config.models import TargetConfig
from netmon.probes.base import Probe, ProbeResult, ProbeStatus, ProbeType


class TCPProbe(Probe):
    probe_type = ProbeType.TCP

    def __init__(self, target: TargetConfig, port: int | None = None) -> None:
        super().__init__(target)
        self.port = port if port is not None else target.port

    async def check(self) -> ProbeResult:
        start = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.target.host, self.port),
                timeout=self.target.timeout_seconds,
            )
        except ConnectionRefusedError as e:
            return self._fail(start, e, error_class="refused")
        except TimeoutError as e:
            return self._fail(start, e, error_class="timeout", status=ProbeStatus.TIMEOUT)
        except (OSError, socket.gaierror) as e:
            # gaierror = DNS failure; other OSError covers network-unreachable etc.
            return self._fail(start, e, error_class="unreachable", status=ProbeStatus.TIMEOUT)
        else:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass  # close errors don't invalidate a successful handshake
            latency_ms = (time.monotonic() - start) * 1000
            return ProbeResult(
                target=self.target.name,
                probe=self.probe_type,
                status=ProbeStatus.OK,
                latency_ms=round(latency_ms, 3),
            )

    def _fail(
        self,
        start: float,
        exc: Exception,
        error_class: str,
        status: ProbeStatus = ProbeStatus.FAILED,
    ) -> ProbeResult:
        return ProbeResult(
            target=self.target.name,
            probe=self.probe_type,
            status=status,
            latency_ms=round((time.monotonic() - start) * 1000, 3),
            error=str(exc) or exc.__class__.__name__,
            error_class=error_class,
        )
