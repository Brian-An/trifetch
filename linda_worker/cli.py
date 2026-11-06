from __future__ import annotations

import argparse
import logging
import os
import signal
from pathlib import Path
from types import FrameType
from urllib.parse import urlsplit

from filelock import FileLock, Timeout

from linda_worker.portal import BrowserConfig
from linda_worker.state import Store
from linda_worker.worker import Worker, WorkerConfig


def positive(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def interrupted(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Route synthetic faxes through the Linda Mini UI.")
    result.add_argument("--url", default=os.environ.get("LINDA_URL", "http://127.0.0.1:8000"))
    result.add_argument("--state-dir", type=Path, default=Path("artifacts/run"))
    result.add_argument("--attempts", type=positive, default=3)
    result.add_argument("--timeout-ms", type=positive, default=10_000)
    result.add_argument("--headed", action="store_true", help="Show the Chromium browser.")
    result.add_argument(
        "--screenshots", action="store_true", help="Save confirmation/verification PNGs."
    )
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    url = arguments.url.rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        parser().error("--url must be an HTTP(S) portal origin without credentials, query or path")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    username = os.environ.get("LINDA_USERNAME", "agent.demo")
    password = os.environ.get("LINDA_PASSWORD", "demo-password-123")
    if not username or not password:
        parser().error("LINDA_USERNAME and LINDA_PASSWORD must not be empty")
    config = WorkerConfig(
        browser=BrowserConfig(
            base_url=url, username=username, password=password, timeout_ms=arguments.timeout_ms
        ),
        state_dir=arguments.state_dir.resolve(),
        attempts=arguments.attempts,
        headed=arguments.headed,
        screenshots=arguments.screenshots,
    )
    config.state_dir.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with FileLock(config.state_dir / "worker.lock", timeout=0):
            store = Store(config.state_dir, f"{url}|{username}")
            try:
                worker = Worker(config, store)
                path = worker.run()
                logging.info("Results: %s", path)
                return 0 if worker.success else 1
            finally:
                store.close()
    except Timeout:
        logging.error("Another worker owns this state directory; wait for it to finish.")
        return 2
    except KeyboardInterrupt:
        logging.warning("Interrupted. Run the same command to reconcile and resume.")
        return 130
    except Exception as exc:
        logging.error("%s", str(exc).replace(password, "[redacted]").splitlines()[0])
        return 1
