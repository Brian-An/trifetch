from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest
from filelock import FileLock
from pydantic import TypeAdapter

from linda_worker.cli import parser
from linda_worker.state import Store

ROOT = Path(__file__).resolve().parents[1]
MARKER_CODE = """
import json, os, psutil
from pathlib import Path
def mark():
    parent = psutil.Process()
    children = [parent] + parent.children(recursive=True)
    data = []
    for process in children:
        try:
            data.append((process.pid, process.create_time()))
        except psutil.NoSuchProcess:
            pass
    Path(os.environ['DEADLINE_MARKER']).write_text(json.dumps(data))
"""
NATIVE_BLOCK = """
import ctypes, subprocess, sys
def native_block(*args, **kwargs):
    subprocess.Popen([sys.executable, '-c',
        'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(3600)'],
        start_new_session=True)
    mark()
    ctypes.PyDLL(None).sleep(3600)
"""


def hook_environment(directory: Path, hook: str) -> tuple[dict[str, str], Path]:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sitecustomize.py").write_text(MARKER_CODE + hook)
    marker = directory / "blocked.json"
    return {
        **os.environ,
        "PYTHONPATH": str(directory) + os.pathsep + str(ROOT),
        "DEADLINE_MARKER": str(marker),
    }, marker


@contextmanager
def command(
    arguments: list[str], env: dict[str, str] | None = None
) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        [sys.executable, "-m", "linda_worker", *arguments],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            with suppress(psutil.NoSuchProcess):
                for child in reversed(psutil.Process(process.pid).children(recursive=True)):
                    with suppress(psutil.NoSuchProcess):
                        child.kill()
            process.kill()
            process.communicate(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def active_processes(marker: Path) -> list[int]:
    saved = TypeAdapter(list[tuple[int, float]]).validate_json(marker.read_bytes())
    active = []
    for pid, created in saved:
        with suppress(psutil.NoSuchProcess):
            process = psutil.Process(pid)
            if process.create_time() == created and process.status() != psutil.STATUS_ZOMBIE:
                active.append(pid)
    return active


def assert_stopped(marker: Path) -> None:
    until = time.monotonic() + 2
    while active_processes(marker) and time.monotonic() < until:
        time.sleep(0.02)
    assert active_processes(marker) == [], "Deadline left browser/OCR descendants running."


def assert_resumed(url: str, directory: Path) -> None:
    with command(["--url", url, "--state-dir", str(directory), "--deadline-seconds", "120"]) as job:
        output, _ = job.communicate(timeout=125)
        assert job.returncode == 0, output
    store = Store(directory, f"{url}|agent.demo")
    try:
        records = store.all_records()
        assert len(records) == 12
        assert all(record.verification == "verified" for record in records)
        assert all(record.submit_attempts <= 1 for record in records)
        assert next(r for r in records if r.fax.fax_id == "FX-210").action == "skipped_duplicate"
    finally:
        store.close()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_deadline_rejects_nonfinite_or_nonpositive_budgets(value: str) -> None:
    with pytest.raises(SystemExit) as error:
        parser().parse_args([f"--deadline-seconds={value}"])
    assert error.value.code == 2


def test_deadline_default_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINDA_DEADLINE_SECONDS", raising=False)
    assert parser().parse_args([]).deadline_seconds == 900
    monkeypatch.setenv("LINDA_DEADLINE_SECONDS", "2.5")
    assert parser().parse_args([]).deadline_seconds == 2.5


@pytest.mark.e2e
@pytest.mark.skipif(os.name != "posix", reason="Native libc block runs on macOS/Linux or Docker.")
@pytest.mark.parametrize("phase", ["pdf", "submit", "interrupt"])
def test_deadline_kills_native_work_and_detached_children_then_resumes(
    portal: str, tmp_path: Path, phase: str
) -> None:
    hook = NATIVE_BLOCK
    if phase in ("pdf", "interrupt"):
        hook += "\nfrom linda_worker import ocr\nocr.pdfium.PdfDocument = native_block\n"
    else:
        hook += """
from linda_worker.portal import Session
original_submit = Session.submit
def blocked_submit(self):
    original_submit(self)
    native_block()
Session.submit = blocked_submit
"""
    environment, marker = hook_environment(tmp_path / "hook", hook)
    directory = tmp_path / "worker"
    budget = 6 if phase == "pdf" else 30
    start = time.monotonic()
    with command(
        ["--url", portal, "--state-dir", str(directory), "--deadline-seconds", str(budget)],
        environment,
    ) as job:
        if phase in ("pdf", "interrupt"):
            until = time.monotonic() + 4
            while not marker.exists() and job.poll() is None and time.monotonic() < until:
                time.sleep(0.02)
            assert marker.exists(), "Native parser block was not reached."
            if phase == "interrupt":
                job.terminate()
            else:
                with command(
                    ["--url", portal, "--state-dir", str(directory), "--deadline-seconds", "1"]
                ) as competing:
                    competing_output, _ = competing.communicate(timeout=4)
                    assert competing.returncode == 2, competing_output
        output, _ = job.communicate(timeout=budget + 5)
        assert job.returncode == (130 if phase == "interrupt" else 124), output
    assert time.monotonic() - start < budget + 5
    assert marker.exists(), output
    assert_stopped(marker)
    with FileLock(directory / "worker.lock", timeout=0):
        pass
    report = json.loads((directory / "results.json").read_text())
    assert report["run_status"] == ("interrupted" if phase == "interrupt" else "timed_out")
    assert report["run_error"]
    assert len(report["faxes"]) == 12
    if phase == "submit":
        store = Store(directory, f"{portal}|agent.demo")
        try:
            pending = store.get("FX-201")
            assert pending is not None and pending.submission_pending
            assert pending.submit_attempts == 1
        finally:
            store.close()
    assert_resumed(portal, directory)


@pytest.fixture
def partial_download() -> Iterator[tuple[str, threading.Event]]:
    entered = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'attachment; filename="fax.pdf"')
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            self.wfile.write(b"%PDF-1.7\n")
            self.wfile.flush()
            entered.set()
            release.wait(timeout=15)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/fax.pdf", entered
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.e2e
def test_deadline_stops_a_started_download_that_never_finishes(
    portal: str, tmp_path: Path, partial_download: tuple[str, threading.Event]
) -> None:
    url, entered = partial_download
    hook = f"""
from linda_worker.portal import Session
from playwright.sync_api import Download
original_download = Session.download
def download(self, path):
    self.page.route('**/download', lambda route: route.fulfill(
        status=302, headers={{'Location': {url!r}}}))
    return original_download(self, path)
Session.download = download
original_failure = Download.failure
def failure(self):
    mark()
    return original_failure(self)
Download.failure = failure
"""
    environment, marker = hook_environment(tmp_path / "hook", hook)
    directory = tmp_path / "worker"
    start = time.monotonic()
    with command(
        ["--url", portal, "--state-dir", str(directory), "--deadline-seconds", "6"], environment
    ) as job:
        output, _ = job.communicate(timeout=11)
        assert job.returncode == 124, output
    assert entered.is_set() and marker.exists(), "Download did not start receiving bytes."
    assert time.monotonic() - start < 11
    assert_stopped(marker)
    assert json.loads((directory / "results.json").read_text())["run_status"] == "timed_out"
    assert_resumed(portal, directory)
