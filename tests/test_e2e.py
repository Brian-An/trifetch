from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import Route as BrowserRoute
from playwright.sync_api import sync_playwright

from linda_worker.models import Record
from linda_worker.policy import ROUTES
from linda_worker.portal import BrowserConfig, Session
from linda_worker.state import Store
from linda_worker.worker import Worker, WorkerConfig

pytestmark = pytest.mark.e2e

EXPECTED = {
    "FX-201": "routed",
    "FX-202": "needs_review",
    "FX-203": "routed",
    "FX-204": "routed",
    "FX-205": "routed",
    "FX-206": "routed",
    "FX-207": "routed",
    "FX-208": "routed",
    "FX-209": "needs_review",
    "FX-210": "skipped_duplicate",
    "FX-211": "already_routed",
    "FX-212": "needs_review",
}


def run_worker(url: str, directory: Path) -> list[Record]:
    config = WorkerConfig(
        browser=BrowserConfig(url, "agent.demo", "demo-password-123", 5000),
        state_dir=directory,
        retry_delay=0.05,
    )
    store = Store(directory, f"{url}|agent.demo")
    try:
        worker = Worker(config, store)
        worker.run()
        return worker.records
    finally:
        store.close()


def assert_outcomes(records: list[Record], url: str) -> None:
    assert {r.fax.fax_id: r.action for r in records} == EXPECTED
    assert all(r.verification == "verified" for r in records)
    assert all(r.extraction and len(r.extraction.pages) == r.fax.expected_pages for r in records)
    by_id = {r.fax.fax_id: r for r in records}
    assert by_id["FX-210"].duplicate_of == "FX-201"
    assert by_id["FX-210"].submit_attempts == 0
    assert by_id["FX-211"].submit_attempts == 0
    assert by_id["FX-212"].extraction is not None
    assert set(by_id["FX-212"].extraction.categories) == {"Lab Result", "Imaging Report"}
    assert by_id["FX-202"].extraction is not None
    assert any("CRITICAL" in flag for flag in by_id["FX-202"].extraction.flags)
    # The independent browser is the result oracle, including all three saved dropdown values.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            with Session(browser, BrowserConfig(url, "agent.demo", "demo-password-123")) as session:
                session.authenticate()
                for record in records:
                    fax, observed = session.open_fax(record.fax.fax_id)
                    if record.action == "skipped_duplicate":
                        assert fax.status == "New" and observed is None
                    else:
                        assert fax.status == "Routed" and observed == record.observed
                        assert observed is not None
                        if record.selected:
                            assert observed == record.selected
                session.page.get_by_role("complementary").get_by_role(
                    "link", name="Routed Documents", exact=True
                ).click()
                table = session.page.get_by_role("table", name="Routed documents", exact=True)
                assert table.locator("tbody tr").count() == 11
        finally:
            browser.close()


@pytest.mark.parametrize(
    "portal",
    ["none", "download_503_once", "session_expire_on_route_once", "route_commit_then_503_once"],
    indirect=True,
)
def test_full_queue_and_replay(portal: str, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    directory = tmp_path / "worker"
    records = run_worker(portal, directory)
    assert_outcomes(records, portal)
    first = next(r for r in records if r.fax.fax_id == "FX-201")
    mode = request.node.callspec.params["portal"]  # type: ignore[attr-defined]
    if mode == "session_expire_on_route_once":
        assert first.submit_attempts == 2
    elif mode == "route_commit_then_503_once":
        assert first.submit_attempts == 1
        assert any("503" in event.detail for event in first.events)
    elif mode == "download_503_once":
        assert any(r.errors for r in records)
    initial_counts = {r.fax.fax_id: r.submit_attempts for r in records}
    replay = run_worker(portal, directory)
    assert_outcomes(replay, portal)
    assert {r.fax.fax_id: r.submit_attempts for r in replay} == initial_counts


@pytest.mark.parametrize("crash_after", [False, True])
def test_hard_restart_around_submit_is_reconciled(
    portal: str, tmp_path: Path, crash_after: bool
) -> None:
    directory = tmp_path / "worker"
    script = """
import os
from linda_worker.cli import main
from linda_worker.portal import Session
original = Session.submit
def crash(self):
    if os.environ['CRASH_AFTER'] == '1':
        original(self)
    os._exit(75)
Session.submit = crash
raise SystemExit(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, "--url", portal, "--state-dir", str(directory)],
        env={**os.environ, "CRASH_AFTER": "1" if crash_after else "0"},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 75, result.stderr
    store = Store(directory, f"{portal}|agent.demo")
    interrupted = store.get("FX-201")
    store.close()
    assert interrupted is not None and interrupted.submission_pending
    records = run_worker(portal, directory)
    assert_outcomes(records, portal)
    original_record = next(r for r in records if r.fax.fax_id == "FX-201")
    assert original_record.submit_attempts == (1 if crash_after else 2)


def test_portal_rejects_same_session_and_accepts_fresh_session(portal: str, tmp_path: Path) -> None:
    config = BrowserConfig(portal, "agent.demo", "demo-password-123")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            with Session(browser, config) as first:
                first.authenticate()
                first.open_fax("FX-201")
                first.download(tmp_path / "download.pdf")
                first.prepare_route("FX-201", ROUTES["Referral"], "Referral request evidence.")
                assert "separate login session" in first.submit()
            with Session(browser, config) as second:
                second.authenticate()
                second.open_fax("FX-201")
                second.prepare_route("FX-201", ROUTES["Referral"], "Referral request evidence.")
                second.submit()
                _, observed = second.open_fax("FX-201")
                assert observed == ROUTES["Referral"]
        finally:
            browser.close()


def test_html_attachment_is_rejected_then_recovered(
    portal: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Session.download
    intercepted = False

    def download(session: Session, path: Path) -> None:
        nonlocal intercepted
        if not intercepted:
            intercepted = True
            session.page.route(
                "**/download",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="fax.pdf"'},
                    body="<html>503 Service Unavailable</html>",
                ),
                times=1,
            )
        original(session, path)

    monkeypatch.setattr(Session, "download", download)
    records = run_worker(portal, tmp_path / "worker")
    assert_outcomes(records, portal)
    assert any("not a PDF" in error.detail for r in records for error in r.errors)


def test_permanent_download_failure_holds_queue_then_resumes(
    portal: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Session.download

    def download(session: Session, path: Path) -> None:
        session.page.route(
            "**/faxes/FX-201/download",
            lambda route: route.fulfill(
                status=200,
                content_type="application/pdf",
                headers={"Content-Disposition": 'attachment; filename="fax.pdf"'},
                body="broken",
            ),
        )
        original(session, path)

    with monkeypatch.context() as context:
        context.setattr(Session, "download", download)
        records = run_worker(portal, tmp_path / "worker")
    assert all(r.submit_attempts == 0 for r in records)
    assert all(r.action == "failed" for r in records if r.fax.status == "New")
    recovered = run_worker(portal, tmp_path / "worker")
    assert_outcomes(recovered, portal)


def test_resend_without_matching_original_is_reviewed(
    portal: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "resend.pdf"
    with Image.new("RGB", (1600, 2000), "white") as image:
        ImageDraw.Draw(image).multiline_text(
            (80, 80),
            "FAX RESEND - REFERRAL REQUEST\n\nOriginal Document ID: REF-UNKNOWN\n"
            "Patient: Taylor Smith\nDOB: 01/01/1980\n"
            "This is a retransmission of an earlier document.\n"
            "Please disregard if the original has already been received.\nPage 1 of 1",
            fill="black",
            font=ImageFont.load_default(size=42),
            spacing=20,
        )
        image.save(path, "PDF", resolution=200)
    body = path.read_bytes()
    original = Session.download

    def replace_download(route: BrowserRoute) -> None:
        # Forward the UI request so the portal records its two-session prerequisite.
        response = route.fetch()
        try:
            route.fulfill(
                response=response,
                headers={**response.headers, "content-length": str(len(body))},
                body=body,
            )
        finally:
            response.dispose()

    def download(session: Session, target: Path) -> None:
        session.page.route("**/faxes/FX-210/download", replace_download)
        original(session, target)

    monkeypatch.setattr(Session, "download", download)
    records = run_worker(portal, tmp_path / "worker")
    resend = next(r for r in records if r.fax.fax_id == "FX-210")
    assert resend.action == "needs_review" and resend.verification == "verified"
    assert resend.observed == ROUTES["Needs Review"]
    assert resend.routing_note is not None and "without a confirmed" in resend.routing_note
