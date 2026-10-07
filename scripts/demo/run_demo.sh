#!/usr/bin/env bash
# Reproducible end-to-end demo: healthy fleet -> kill app-01 -> automated
# remediation -> verified recovery -> closed incident. ~2 minutes.
set -euo pipefail

say() { echo; echo "=== $* ==="; sleep 2; }

say "1/6 Fleet is healthy — watch Grafana (localhost:3000) or the API"
curl -s http://localhost:8000/targets | python3 -m json.tool || true

say "2/6 Injecting failure: killing app-01 container"
docker stop app-01

say "3/6 Monitoring detects it (3 consecutive failures -> UNHEALTHY -> incident)"
sleep 35
curl -s http://localhost:8000/incidents | python3 -m json.tool || true

say "4/6 Remediation engine restarts the container (policy: service_down)"
docker start app-01

say "5/6 Verification probes confirm recovery -> RECOVERING -> HEALTHY"
sleep 30

say "6/6 Incident closed; MTTR recorded. Incidents + remediation audit:"
curl -s http://localhost:8000/incidents | python3 -m json.tool || true
curl -s http://localhost:8000/remediations | python3 -m json.tool || true
curl -s http://localhost:8000/reliability | python3 -m json.tool || true

echo
echo "Demo complete. Grafana shows the full timeline under 'Remediation' and 'MTTR'."
