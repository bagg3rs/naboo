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

PIDFILE = Path("/tmp/naboo-agent.pid")


def _kill_existing():
    """Kill any existing Naboo agent process (via pidfile)."""
    if PIDFILE.exists():
        try:
            old_pid = int(PIDFILE.read_text().strip())
            if old_pid != os.getpid():
                os.kill(old_pid, signal.SIGTERM)
                import time; time.sleep(1)  # give it a moment to exit
        except (ProcessLookupError, ValueError):
            pass  # already gone
        PIDFILE.unlink(missing_ok=True)


def main():
    _kill_existing()
    PIDFILE.write_text(str(os.getpid()))

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
        PIDFILE.unlink(missing_ok=True)
        loop.create_task(agent.stop())

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(agent.start())
    except KeyboardInterrupt:
        loop.run_until_complete(agent.stop())
    finally:
        PIDFILE.unlink(missing_ok=True)
        loop.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
