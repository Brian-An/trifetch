from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from playwright.sync_api import Browser

from linda_worker.models import Fax, Record
from linda_worker.policy import ROUTES
from linda_worker.portal import BrowserConfig, PortalError
from linda_worker.state import Store, atomic_json
from linda_worker.worker import Worker, WorkerConfig


def config(directory: Path) -> WorkerConfig:
    return WorkerConfig(
        browser=BrowserConfig("http://127.0.0.1:8000", "user", "password"),
        state_dir=directory,
        retry_delay=0,
    )


def test_conflicting_saved_assignment_stays_a_conflict_on_replay(tmp_path: Path) -> None:
    store = Store(tmp_path, "test")
    try:
        worker = Worker(config(tmp_path), store)
        record = Record(
            fax=Fax(
                fax_id="F1",
                received_at="2026-11-03 08:00",
                sender="Sender",
                expected_pages=1,
                status="Routed",
            ),
            selected=ROUTES["Referral"],
            submit_attempts=1,
            submission_pending=True,
        )
        worker.accept_observed(record, ROUTES["Lab Result"])
        assert record.verification == "conflict" and record.action == "failed"
        store.save(record)
        replay = store.get("F1")
        assert replay is not None
        replay.verification = "pending"
        worker.accept_observed(replay, ROUTES["Lab Result"])
        assert replay.verification == "conflict" and replay.action == "failed"
    finally:
        store.close()


def test_unavailable_ui_is_unknown_even_if_last_known_status_was_new(tmp_path: Path) -> None:
    store = Store(tmp_path, "test")
    try:
        worker = Worker(config(tmp_path), store)
        record = Record(
            fax=Fax(
                fax_id="F1",
                received_at="2026-11-03 08:00",
                sender="Sender",
                expected_pages=1,
                status="New",
            )
        )
        record.event("reconciled", "A previous observation showed New.")
        worker.records = [record]
        with patch("linda_worker.worker.Session", side_effect=PortalError("Portal unavailable")):
            assert worker.verify(cast(Browser, object()), record) == "unknown"
        assert record.verification == "pending"
        assert record.errors[-1].stage == "verify"
    finally:
        store.close()


def test_early_startup_failure_replaces_stale_success_report(tmp_path: Path) -> None:
    store = Store(tmp_path, "test")
    try:
        atomic_json(tmp_path / "results.json", {"run_status": "complete"})
        worker = Worker(config(tmp_path), store)
        with (
            patch("linda_worker.worker.ensure_ocr", side_effect=RuntimeError("Missing OCR")),
            pytest.raises(RuntimeError, match="Missing OCR"),
        ):
            worker.run()
        assert json.loads((tmp_path / "results.json").read_text())["run_status"] == "failed"
        assert not worker.success
    finally:
        store.close()
