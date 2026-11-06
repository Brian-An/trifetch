from __future__ import annotations

import json
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from linda_worker.models import Fax, Record
from linda_worker.policy import ROUTES
from linda_worker.state import Store, atomic_json


def test_submit_intent_survives_reopening_without_duplicate_report_rows(tmp_path: Path) -> None:
    record = Record(
        fax=Fax(
            fax_id="F-1",
            received_at="2026-11-03 08:00",
            sender="Sender",
            expected_pages=1,
            status="New",
        ),
        selected=ROUTES["Referral"],
        submission_pending=True,
        submit_attempts=1,
    )
    store = Store(tmp_path, "portal|user")
    store.save(record)
    store.close()
    resumed = Store(tmp_path, "portal|user")
    try:
        saved = resumed.get("F-1")
        assert saved == record
        assert saved is not None
        saved.action = "routed"
        saved.submission_pending = False
        saved.verification = "verified"
        resumed.save(saved)
        path = resumed.export([saved], "complete")
        report = json.loads(path.read_text())
        assert len(report["faxes"]) == 1
        assert report["faxes"][0]["submit_attempts"] == 1
        assert resumed.get("F-1") == saved
    finally:
        resumed.close()


def test_state_cannot_be_reused_for_a_different_target(tmp_path: Path) -> None:
    Store(tmp_path, "portal-a|user").close()
    with pytest.raises(ValueError, match="different portal"):
        Store(tmp_path, "portal-b|user")


def test_atomic_report_replaces_previous_content_without_temporary_files(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    atomic_json(path, {"value": 1})
    atomic_json(path, {"value": 2})
    assert json.loads(path.read_text()) == {"value": 2}
    assert path.stat().st_mode & 0o444 == 0o444
    assert sorted(p.name for p in tmp_path.iterdir()) == ["results.json"]


def test_concurrent_worker_is_rejected_without_waiting(tmp_path: Path) -> None:
    path = tmp_path / "worker.lock"
    with FileLock(path, timeout=0), pytest.raises(Timeout), FileLock(path, timeout=0):
        pytest.fail("Second lock must not be acquired.")
