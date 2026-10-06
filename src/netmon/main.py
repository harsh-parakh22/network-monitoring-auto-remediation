"""Entry point: wires config, engine, metrics endpoint and graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from prometheus_client import start_http_server

from netmon.config.models import load_config
from netmon.engine import MonitorEngine
from netmon.logging_setup import setup_logging

logger = logging.getLogger("netmon.main")


async def main() -> None:
    setup_logging(os.environ.get("NETMON_LOG_LEVEL", "INFO"))
    config_path = os.environ.get("NETMON_CONFIG", "/etc/netmon/config.yaml")
    metrics_port = int(os.environ.get("NETMON_METRICS_PORT", "9090"))
    cfg = load_config(config_path)
    logger.info("starting monitor", extra={"targets": len(cfg.targets), "config": config_path})

    engine = MonitorEngine(cfg)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    metrics_server, _ = await start_http_server(metrics_port)  # type: ignore[misc]
    try:
        await engine.run()
    finally:
        metrics_server.close()
        logger.info("monitor stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
