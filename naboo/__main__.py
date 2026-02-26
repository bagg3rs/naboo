"""
Run with:
    uv run python -m naboo
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from naboo.agent import NabooAgent


def main():
    # Log to both console and file (/tmp/naboo.log for easy inspection)
    log_file = os.getenv("NABOO_LOG_FILE", "/tmp/naboo.log")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logger = logging.getLogger("naboo")
    logger.info("Starting Naboo...")

    agent = NabooAgent()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(sig, frame):
        logger.info(f"Signal {sig} received, shutting down...")
        loop.create_task(agent.stop())

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(agent.start())
    except KeyboardInterrupt:
        loop.run_until_complete(agent.stop())
    finally:
        loop.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
