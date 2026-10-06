"""Unit tests for probes.

TCP and HTTP tests use real sockets/servers on loopback — no mocking of
the code under test. ICMP tests run only on Linux (CI and containers):
the Windows ping binary has incompatible flags; ICMP behavior is
exercised properly inside the Docker stack anyway.
"""

from __future__ import annotations

import asyncio
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from netmon.config.models import ProbeType, TargetConfig
from netmon.probes.base import ProbeStatus
from netmon.probes.http import HTTPProbe
from netmon.probes.tcp import TCPProbe


def make_target(**overrides) -> TargetConfig:
    base = dict(name="test", host="127.0.0.1", port=8080, checks=[ProbeType.TCP], timeout_seconds=2.0)
    base.update(overrides)
    return TargetConfig(**base)


# ---------------------------------------------------------------- TCP


async def test_tcp_success_against_real_listener():
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        result = await TCPProbe(make_target(port=port)).check()
    finally:
        server.close()
        await server.wait_closed()
    assert result.success
    assert result.latency_ms is not None and result.latency_ms >= 0


@pytest.mark.skipif(sys.platform != "linux", reason="RST-on-closed-port is deterministic only on Linux")
async def test_tcp_refused_when_port_closed():
    # Bind a port, then close it: nothing is listening there now.
    import socket as _s

    tmp = _s.socket()
    tmp.bind(("127.0.0.1", 0))
    port = tmp.getsockname()[1]
    tmp.close()
    result = await TCPProbe(make_target(port=port)).check()
    assert result.status is ProbeStatus.FAILED
    assert result.error_class == "refused"


async def test_tcp_timeout_against_nonresponsive_host():
    # TEST-NET-3 (203.0.113.0/24) is guaranteed non-routable per RFC 5737.
    target = make_target(host="203.0.113.1", port=9999, timeout_seconds=1.0)
    result = await TCPProbe(target).check()
    assert result.status is ProbeStatus.TIMEOUT
    assert result.error_class == "timeout"


# ---------------------------------------------------------------- HTTP


class _Handler(BaseHTTPRequestHandler):
    status = 200
    body = b'{"status": "ok"}'

    def do_GET(self):  # noqa: N802
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture()
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


async def test_http_success(http_server):
    target = make_target(port=http_server, checks=[ProbeType.HTTP], http_expected_body="ok")
    result = await HTTPProbe(target).check()
    assert result.success
    assert result.http_status == 200


async def test_http_5xx_is_failure(http_server):
    _Handler.status = 503
    try:
        target = make_target(port=http_server, checks=[ProbeType.HTTP])
        result = await HTTPProbe(target).check()
    finally:
        _Handler.status = 200
    assert not result.success
    assert result.error_class == "http_503"
    assert result.http_status == 503


async def test_http_body_mismatch_is_failure(http_server):
    target = make_target(port=http_server, checks=[ProbeType.HTTP], http_expected_body="definitely-not-present")
    result = await HTTPProbe(target).check()
    assert not result.success
    assert result.error == "body mismatch"


async def test_http_connection_error(http_server):
    # Same recipe as the TCP refused test: a bound-then-closed port.
    import socket as _s

    tmp = _s.socket()
    tmp.bind(("127.0.0.1", 0))
    port = tmp.getsockname()[1]
    tmp.close()
    result = await HTTPProbe(make_target(port=port, checks=[ProbeType.HTTP])).check()
    assert not result.success
    assert result.error_class in ("connection", "timeout")


# ---------------------------------------------------------------- ICMP


@pytest.mark.skipif(sys.platform != "linux", reason="ICMP probe verified on Linux/containers")
async def test_icmp_localhost_succeeds():
    from netmon.probes.icmp import ICMPProbe

    result = await ICMPProbe(make_target(host="127.0.0.1", checks=[ProbeType.ICMP])).check()
    assert result.success
    assert result.latency_ms is not None


def test_icmp_rtt_regex_parses_linux_ping_output():
    from netmon.probes.icmp import _RTT_RE

    line = "64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.045 ms"
    assert _RTT_RE.search(line).group(1) == "0.045"
