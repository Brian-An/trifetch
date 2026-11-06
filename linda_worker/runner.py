from __future__ import annotations

import logging
import sys

from pydantic import TypeAdapter

from linda_worker.config import WorkerConfig


def run(config: WorkerConfig) -> int:
    # Native OCR/PDF and browser imports stay inside the supervised process.
    from linda_worker.state import Store
    from linda_worker.worker import Worker

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    store = Store(config.state_dir, f"{config.browser.base_url}|{config.browser.username}")
    try:
        worker = Worker(config, store)
        path = worker.run()
        logging.info("Results: %s", path)
        return 0 if worker.success else 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        message = str(exc).replace(config.browser.password, "[redacted]")
        logging.error("%s", (message.splitlines() or [type(exc).__name__])[0])
        return 1
    finally:
        store.close()


def main() -> int:
    try:
        config = TypeAdapter(WorkerConfig).validate_json(sys.stdin.buffer.read())
    except ValueError:
        logging.error("Invalid worker configuration.")
        return 1
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
