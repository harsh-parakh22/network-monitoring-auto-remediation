# The Networking Behind the System

The classifier is a TCP/IP lesson in executable form. Four failure modes,
four different layers of the stack, four different remediation decisions.

## The failure matrix

| ICMP | TCP | HTTP | Class | What physically happened |
|---|---|---|---|---|
| fail | timeout | fail | `host_unreachable` | IP packets get no reply — host down, cable out, routing broken, or firewall DROPs everything. Network layer (L3). |
| ok | refused | fail | `service_down` | ICMP replies fine (host is up), but our TCP SYN got a **RST** back — the kernel is saying *nobody is listening on that port*. The process died. Transport layer (L4). |
| ok | timeout | fail | `port_filtered` | Host answers ping but our SYN vanishes — a firewall is silently DROPping (not rejecting) the port. |
| ok | ok | 5xx | `app_unhealthy` | Full TCP handshake completes, HTTP request gets a real response — the wrong one. Transport is perfect; the application (L7) is broken. |

## Why refused ≠ timeout (the interview favorite)

- **Connection refused** = the remote host *responded* to our SYN with a TCP
  RST packet. Round trip completed. This is active information: host alive,
  port closed. Diagnosis: process not running → restart the service.
- **Connection timeout** = we sent SYN(s), nothing ever came back. Could be
  the host, the network in between, or a firewall configured to DROP.
  Diagnosis: infrastructure problem → restarting a service is pointless.

`TCPProbe` preserves this distinction: `ConnectionRefusedError` →
`error_class="refused"`, timeout → `"timeout"`. `FailureClassifier` reads
exactly this field when deciding between `service_down` (restartable) and
`port_filtered` (not restartable — restarting won't open a firewall).

## The TCP handshake in `TCPProbe`

`asyncio.open_connection` performs the full three-way handshake:

```text
client                    server
  │──── SYN (seq=x) ───────▶│
  │◀── SYN+ACK (x+1, y) ────│
  │──── ACK (y+1) ─────────▶│
```

Latency measured by the probe ≈ one SYN→SYN+ACK round trip — the same
number `ss -ti` would show as RTT for that connection. After measuring, we
close cleanly (FIN/ACK) — the probe connects and *immediately* disconnects;
it verifies listening, it doesn't consume the service.

## ICMP and privileges

`ping` sends an ICMP Echo Request (type 8) and waits for Echo Reply
(type 0). Crafting ICMP needs a raw socket, which needs **CAP_NET_RAW** —
normally root-only. Running an entire monitor as root to get ping is a
privilege-escalation antipattern. Instead: the container gets exactly one
extra capability (`cap_add: NET_RAW`) and uses the system `ping` binary,
which the kernel permits to send ICMP. Least privilege, real ICMP.

## Packet loss and latency measurement

`chaos-01` runs Linux **tc/netem**: `tc qdisc add dev eth0 root netem
delay 300ms 50ms loss 30%` degrades at the kernel's egress queue — the same
mechanism real WAN links exhibit, not an application sleep(). The monitor's
`netmon_packet_loss_ratio` is a sliding-window ratio of failed ICMP probes
(20-sample window by default), which is how loss is actually measured in
production synthetic monitoring.

## DNS, ports, and the demo network

Compose service names (`app-01` etc.) are resolvable because Docker runs an
embedded DNS server at 127.0.0.11 on the user-defined bridge network — the
same pattern as a private DNS zone in a real network. Targets are
configured by *name*, never IP, so the topology can be renumbered freely.
