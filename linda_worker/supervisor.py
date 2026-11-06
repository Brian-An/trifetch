from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

import psutil
from pydantic import TypeAdapter

from linda_worker.config import WorkerConfig
from linda_worker.state import Store


class ProcessTree:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.processes: dict[int, psutil.Process] = {}
        with suppress(psutil.NoSuchProcess):
            self.processes[pid] = psutil.Process(pid)

    def remember(self) -> None:
        root = self.processes.get(self.pid)
        if root is not None:
            with suppress(psutil.NoSuchProcess):
                for child in root.children(recursive=True):
                    self.processes[child.pid] = child

    def stop(self) -> None:
        # Chromium can create a separate process group; track descendants as well as the group.
        # Freeze parents before rescanning so they cannot create children during cleanup.
        for _ in range(2):
            for process in list(self.processes.values()):
                with suppress(psutil.NoSuchProcess):
                    process.suspend()
                    for child in process.children(recursive=True):
                        self.processes[child.pid] = child
        root = self.processes.get(self.pid)
        if os.name == "posix" and root is not None and root.is_running():
            with suppress(ProcessLookupError):
                os.killpg(self.pid, signal.SIGKILL)
        for process in reversed(list(self.processes.values())):
            with suppress(psutil.NoSuchProcess):
                process.kill()
        psutil.wait_procs(list(self.processes.values()), timeout=1)


def publish_interruption(config: WorkerConfig, status: str, message: str) -> None:
    store = Store(config.state_dir, f"{config.browser.base_url}|{config.browser.username}")
    try:
        # Verification timestamps describe saved observations; submit intent stays intact.
        store.export(store.all_records(), status, error=message)
    finally:
        store.close()


def supervise(config: WorkerConfig, deadline_seconds: float) -> int:
    deadline = time.monotonic() + deadline_seconds
    # Credentials travel only through the private pipe, never arguments or temporary files.
    payload: bytes | None = TypeAdapter(WorkerConfig).dump_json(config)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [sys.executable, "-m", "linda_worker.runner"],
        stdin=subprocess.PIPE,
        start_new_session=os.name == "posix",
        creationflags=creationflags,
    )
    tree = ProcessTree(process.pid)
    status: str | None = None
    message = ""
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, deadline_seconds)
            try:
                process.communicate(payload, timeout=min(0.2, remaining))
                outcome = process.returncode
                break
            except subprocess.TimeoutExpired:
                payload = None
                tree.remember()
    except subprocess.TimeoutExpired:
        outcome = 124
        status = "timed_out"
        message = (
            f"Processing deadline of {deadline_seconds:g}s exceeded. "
            "Saved outcomes require UI reconciliation; rerun to resume safely."
        )
    except KeyboardInterrupt:
        outcome = 130
        status = "interrupted"
        message = "Interrupted. Rerun the same command to reconcile and resume."
    finally:
        tree.stop()
        process.wait(timeout=1)
        if process.stdin is not None:
            process.stdin.close()
    if status is not None:
        publish_interruption(config, status, message)
        logging.error("%s", message)
    elif outcome < 0:
        outcome = 128 - outcome
        publish_interruption(config, "failed", f"Worker terminated with exit code {outcome}.")
    return outcome
