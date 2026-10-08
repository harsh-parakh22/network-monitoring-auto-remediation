"""Entry point: wires config, orchestrator (engine + remediation + store),
metrics endpoint, the operational API, and graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

import uvicorn
from prometheus_client import start_http_server

from netmon.api.app import app as api_app
from netmon.api.app import set_orchestrator
from netmon.config.models import load_config
from netmon.logging_setup import setup_logging
from netmon.orchestrator import Orchestrator
from netmon.remediation.engine import RemediationEngine
from netmon.storage.store import IncidentStore

logger = logging.getLogger("netmon.main")


async def main() -> None:
    setup_logging(os.environ.get("NETMON_LOG_LEVEL", "INFO"))
    config_path = os.environ.get("NETMON_CONFIG", "/etc/netmon/config.yaml")
    metrics_port = int(os.environ.get("NETMON_METRICS_PORT", "9090"))
    api_port = int(os.environ.get("NETMON_API_PORT", "8000"))
    data_dir = Path(os.environ.get("NETMON_DATA_DIR", "/app/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_config(config_path)
    logger.info("starting monitor", extra={"targets": len(cfg.targets), "config": config_path})

    store = IncidentStore(data_dir / "netmon.db")
    remediation = RemediationEngine(cfg.remediation)
    orch = Orchestrator(cfg, store, remediation)
    set_orchestrator(orch)

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # Synchronous: starts the WSGRefactory server in a daemon thread.
    metrics_server, _ = start_http_server(metrics_port)
    # Bind all interfaces: this is a container serving Prometheus/API on the
    # internal docker networks.
    # Container serving Prometheus/API on internal docker networks.
    uv_config = uvicorn.Config(
        api_app, host="0.0.0.0", port=api_port, log_level="warning"  # noqa: S104
    )
    uv_server = uvicorn.Server(uv_config)
    api_task = asyncio.create_task(uv_server.serve())

    try:
        await orch.run()
    finally:
        uv_server.should_exit = True
        await api_task
        metrics_server.shutdown()
        metrics_server.server_close()
        store.close()
        logger.info("monitor stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
