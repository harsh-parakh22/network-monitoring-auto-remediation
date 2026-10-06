"""ICMP reachability probe.

Raw ICMP sockets require CAP_NET_RAW / root. Running the whole monitor as
root violates least privilege, so this probe shells out to the system
``ping`` binary (parsed, fixed argv — never a shell string) and relies on
ping's own setuid/capability grants. A single ICMP echo round trip,
not the ping binary's statistics, is the measurement: we pass -c 1 and
parse the reply time.

Exit codes: 0 = reply received, 1 = no reply (packet loss / unreachable),
non-0/1 (67+) = ping could not run (bad flags, DNS failure, permissions).
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time

from netmon.config.models import TargetConfig
from netmon.probes.base import Probe, ProbeResult, ProbeStatus, ProbeType

_RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms")


class ICMPProbe(Probe):
    probe_type = ProbeType.ICMP

    def __init__(self, target: TargetConfig, binary: str | None = None) -> None:
        super().__init__(target)
        resolved = shutil.which(binary or "ping")
        if resolved is None:
            raise RuntimeError("ping binary not found on PATH; ICMP probing unavailable")
        self._binary = resolved

    async def check(self) -> ProbeResult:
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            self._binary,
            "-c", "1",
            "-W", str(max(1, int(self.target.timeout_seconds))),
            self.target.host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=self.target.timeout_seconds + 2
            )
        except TimeoutError:
            proc.kill()
            return ProbeResult(
                target=self.target.name, probe=self.probe_type, status=ProbeStatus.TIMEOUT,
                latency_ms=None, error="ping timed out", error_class="timeout",
                timestamp=start,
            )

        out = stdout.decode(errors="replace")
        if proc.returncode == 0 and (m := _RTT_RE.search(out)):
            return ProbeResult(
                target=self.target.name, probe=self.probe_type, status=ProbeStatus.OK,
                latency_ms=float(m.group(1)), timestamp=start,
            )
        if proc.returncode == 1:
            return ProbeResult(
                target=self.target.name, probe=self.probe_type, status=ProbeStatus.TIMEOUT,
                latency_ms=None, error="no ICMP reply", error_class="unreachable",
                timestamp=start,
            )
        # ping itself failed to run (DNS, permissions, bad flags)
        return ProbeResult(
            target=self.target.name, probe=self.probe_type, status=ProbeStatus.ERROR,
            latency_ms=None, error=f"ping exited {proc.returncode}", error_class="probe_error",
            timestamp=start,
        )
