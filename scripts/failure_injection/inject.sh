#!/usr/bin/env bash
# Failure injection scenarios for demos and testing.
# Usage: ./scripts/failure_injection/inject.sh <scenario>
set -euo pipefail

MONITOR_URL="${MONITOR_URL:-http://localhost:8000}"

scenario_help() {
  cat <<'EOF'
Scenarios:
  container-stop   Scenario 1: stop app-01 container (service_down -> remediation)
  port-fail        Scenario 2: stop db-01 (TCP port closed, ICMP ok)
  latency          Scenario 3: add 300ms +-50ms delay on chaos-01 eth0
  packet-loss      Scenario 4: drop 30% of packets on chaos-01 eth0
  netem-clear      Remove any tc/netem rules from chaos-01
  http-fail        Scenario 6: make app-02 /health return 503 (app_unhealthy)
  crash-process    Scenario 5: kill app-01 process without stopping container
  http-recover     Undo http-fail
  recover-all      Restore everything to normal
EOF
}

scenario_container_stop() {
  # Kill the SERVICE process inside app-01 while the container (host) stays
  # up: ICMP ok + TCP refused -> service_down -> restart_container remediation.
  echo ">> Crashing app-01 service process (container stays up -> service_down -> auto-restart)"
  docker exec app-01 python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080/crash', method='POST'))"
}

scenario_port_fail() {
  echo ">> Stopping db-01 (expect: TCP refused while ICMP ok -> service_down)"
  docker stop db-01
}

scenario_latency() {
  echo ">> Injecting 300ms +-50ms latency on chaos-01"
  docker exec chaos-01 tc qdisc add dev eth0 root netem delay 300ms 50ms
}

scenario_packet_loss() {
  echo ">> Injecting 30% packet loss on chaos-01"
  docker exec chaos-01 tc qdisc add dev eth0 root netem loss 30%
}

scenario_netem_clear() {
  echo ">> Removing netem rules from chaos-01"
  docker exec chaos-01 tc qdisc del dev eth0 root 2>/dev/null || true
}

scenario_http_fail() {
  echo ">> Making app-02 /health return 503 (expect: app_unhealthy)"
  docker exec app-02 python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080/fail', method='POST'))"
}

scenario_http_recover() {
  docker exec app-02 python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080/recover', method='POST'))"
  echo ">> app-02 health restored"
}

scenario_crash_process() {
  echo ">> Crashing app-02 service process (same as container-stop, other target)"
  docker exec app-02 python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080/crash', method='POST'))"
}

scenario_recover_all() {
  scenario_netem_clear
  scenario_http_recover || true
  docker start app-01 db-01 2>/dev/null || true
  echo ">> Everything restored"
}

case "${1:-help}" in
  container-stop) scenario_container_stop ;;
  port-fail)      scenario_port_fail ;;
  latency)        scenario_latency ;;
  packet-loss)    scenario_packet_loss ;;
  netem-clear)    scenario_netem_clear ;;
  http-fail)      scenario_http_fail ;;
  http-recover)   scenario_http_recover ;;
  crash-process)  scenario_crash_process ;;
  recover-all)    scenario_recover_all ;;
  *)              scenario_help ;;
esac

echo
echo "Watch it happen:  docker compose logs -f monitor"
echo "Dashboard:        ${MONITOR_URL}/targets  |  Grafana http://localhost:3000"
