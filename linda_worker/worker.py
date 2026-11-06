from __future__ import annotations

import hashlib
import logging
import time
from contextlib import suppress
from pathlib import Path
from typing import Literal

from playwright.sync_api import Browser, Error, sync_playwright

from linda_worker.config import WorkerConfig as WorkerConfig
from linda_worker.models import Fax, Record, Route, timestamp
from linda_worker.ocr import InvalidPDF, ensure_ocr, extract_pages, validate_pdf
from linda_worker.policy import ROUTES, classify, duplicate_plan, received_key, routing_note
from linda_worker.portal import PortalError, Session
from linda_worker.state import Store

log = logging.getLogger(__name__)
RECOVERABLE = (Error, PortalError, InvalidPDF, OSError, ValueError)


class Worker:
    def __init__(self, config: WorkerConfig, store: Store) -> None:
        self.config = config
        self.store = store
        self.records: list[Record] = []
        self.success = False

    def save(self, record: Record) -> None:
        self.store.save(record)
        self.store.export(self.records, "running")

    def error(self, record: Record, stage: str, exc: Exception) -> None:
        detail = str(exc).replace(self.config.browser.password, "[redacted]")[:3000]
        record.error(stage, detail)
        summary = (detail.splitlines() or [type(exc).__name__])[0]
        log.warning("%s: %s: %s", record.fax.fax_id, stage, summary)
        self.save(record)

    def pause(self, attempt: int) -> None:
        time.sleep(min(5, self.config.retry_delay * 2**attempt))

    @staticmethod
    def filename(fax_id: str) -> str:
        return hashlib.sha256(fax_id.encode()).hexdigest()[:24]

    def capture(self, session: Session, record: Record, stage: str) -> None:
        if self.config.screenshots:
            try:
                session.screenshot(
                    self.config.state_dir
                    / "screenshots"
                    / f"{self.filename(record.fax.fax_id)}-{stage}.png"
                )
            except Error as exc:
                self.error(record, "screenshot", exc)

    def discover(self, browser: Browser) -> list[Fax]:
        for attempt in range(self.config.attempts):
            try:
                with Session(browser, self.config.browser) as session:
                    session.authenticate()
                    return session.discover()
            except RECOVERABLE:
                if attempt + 1 == self.config.attempts:
                    raise
                self.pause(attempt)
        raise AssertionError("Discovery attempts must be positive.")

    def ingest(self, browser: Browser, record: Record, engine: str) -> bool:
        path = self.config.state_dir / "downloads" / f"{self.filename(record.fax.fax_id)}.pdf"
        for attempt in range(self.config.attempts):
            try:
                with Session(browser, self.config.browser) as session:
                    session.authenticate()
                    fax, observed = session.open_fax(record.fax.fax_id)
                    record.fax = fax
                    record.observed = observed
                    if observed is not None:
                        self.accept_observed(record, observed)
                    # New faxes are downloaded on each run to renew the two-session prerequisite,
                    # including when the reviewer resets the sandbox but keeps worker checkpoints.
                    cached = False
                    if observed and record.extraction and path.exists():
                        with suppress(InvalidPDF):
                            cached = validate_pdf(path) == record.pdf_sha256
                    if not cached:
                        session.download(path)
                        digest = validate_pdf(path)
                        record.event(
                            "downloaded",
                            "Downloaded and validated PDF through the UI.",
                            session.context_id,
                        )
                        record.downloaded_at = timestamp()
                        record.downloaded_fax = fax.model_copy(deep=True)
                        record.pdf_path = str(path.relative_to(self.config.state_dir))
                        if record.pdf_sha256 != digest or record.extraction is None:
                            record.extraction = classify(
                                extract_pages(path), fax.expected_pages, engine
                            )
                        record.pdf_sha256 = digest
                    if record.extraction is None:
                        raise PortalError("No extraction is available for this fax.")
                    if len(record.extraction.pages) != fax.expected_pages:
                        record.extraction.classification = "Needs Review"
                        warning = "Current portal page count differs from OCR page count."
                        if warning not in record.extraction.uncertainties:
                            record.extraction.uncertainties.append(warning)
                    self.save(record)
                    log.info(
                        "%s: extracted %s (%s pages)",
                        fax.fax_id,
                        record.extraction.classification,
                        len(record.extraction.pages),
                    )
                    return True
            except RECOVERABLE as exc:
                self.error(record, "ingest", exc)
                if attempt + 1 < self.config.attempts:
                    self.pause(attempt)
        if record.observed is None:
            record.action = "failed"
            record.verification = "failed"
        self.save(record)
        return False

    def accept_observed(self, record: Record, observed: Route) -> None:
        record.observed = observed
        if record.selected and record.submit_attempts > 0:
            if observed != record.selected:
                record.action = "failed"
                record.verification = "conflict"
                record.error(
                    "verify",
                    "Saved UI assignment conflicts with checkpointed intent; "
                    "completed assignment is left untouched.",
                )
            else:
                record.action = (
                    "needs_review" if observed.document_type == "Needs Review" else "routed"
                )
                record.verification = "verified"
        else:
            record.action = "already_routed"
            record.verification = "verified"
        record.submission_pending = False
        record.verified_at = timestamp()
        record.event("verified", "Read completed assignment and all three saved values in the UI.")

    def route(self, browser: Browser, record: Record, planned: Route, note: str) -> None:
        for attempt in range(self.config.attempts):
            try:
                # Each context authenticates after ingestion finishes downloading the PDFs.
                with Session(browser, self.config.browser) as session:
                    session.authenticate()
                    fax, observed = session.open_fax(record.fax.fax_id)
                    record.fax = fax
                    if observed is not None:
                        self.accept_observed(record, observed)
                        self.capture(session, record, "verified")
                        self.save(record)
                        return
                    if record.downloaded_fax and (
                        record.downloaded_fax.model_dump(exclude={"status"})
                        != fax.model_dump(exclude={"status"})
                    ):
                        raise PortalError(
                            "Transmission metadata changed after download; "
                            "rerun to extract the current document."
                        )
                    record.event(
                        "reconciled",
                        "Fresh UI inspection shows New; safe to prepare a route.",
                        session.context_id,
                    )
                    record.submission_pending = False
                    record.selected = planned
                    record.routing_note = note
                    session.prepare_route(fax.fax_id, planned, note)
                    self.capture(session, record, "confirmation")
                    record.submission_pending = True
                    record.submit_attempts += 1
                    record.event(
                        "submit_intent",
                        "Confirmed exact fax ID and three dropdown values.",
                        session.context_id,
                    )
                    self.save(record)
                    message = session.submit()
                    record.event(
                        "submit_returned", message or "Submit returned; UI verification pending."
                    )
                    self.save(record)
                # The next fresh context reconciles the result even when submit returned normally.
                if self.verify(browser, record) == "routed":
                    return
            except RECOVERABLE as exc:
                self.error(record, "route", exc)
                # A click exception can follow a committed route. Verification precedes every retry.
                if self.verify(browser, record) == "routed":
                    return
            if attempt + 1 < self.config.attempts:
                self.pause(attempt)
        record.action = "failed"
        record.verification = "failed"
        record.error("route", "Retry budget exhausted; rerun to reconcile or retry safely.")
        self.save(record)

    def verify(self, browser: Browser, record: Record) -> Literal["routed", "new", "unknown"]:
        try:
            with Session(browser, self.config.browser) as session:
                session.authenticate()
                fax, observed = session.open_fax(record.fax.fax_id)
                record.fax = fax
                if observed is None:
                    record.event("reconciled", "Fresh UI inspection shows no completed route.")
                    self.save(record)
                    return "new"
                self.accept_observed(record, observed)
                self.capture(session, record, "verified")
                self.save(record)
                return "routed"
        except RECOVERABLE as exc:
            self.error(record, "verify", exc)
            return "unknown"

    def run(self) -> Path:
        self.store.export(self.records, "running")
        try:
            engine = ensure_ocr()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=not self.config.headed)
                try:
                    faxes = self.discover(browser)
                    for fax in faxes:
                        record = self.store.get(fax.fax_id) or Record(fax=fax)
                        record.fax = fax
                        record.observed = None
                        record.verification = "pending"
                        record.verified_at = None
                        self.records.append(record)
                        self.store.save(record)
                    log.info("Discovered %s transmissions across all inbox pages.", len(faxes))
                    complete = True
                    for record in self.records:
                        complete = self.ingest(browser, record, engine) and complete
                    duplicates, reviews = duplicate_plan(self.records)
                    by_id = {r.fax.fax_id: r for r in self.records}
                    try:
                        ordered = sorted(self.records, key=received_key)
                    except (ValueError, TypeError):
                        ordered = self.records
                        reviews.update(
                            {
                                r.fax.fax_id: "Received time is ambiguous; manual review required."
                                for r in self.records
                            }
                        )
                        duplicates = {
                            fax_id: matched_id
                            for fax_id, matched_id in duplicates.items()
                            if by_id[matched_id].observed is not None
                            and by_id[matched_id].verification == "verified"
                        }
                    for record in ordered:
                        if record.observed is not None:
                            continue
                        if not complete:
                            record.action = "failed"
                            record.verification = "failed"
                            record.error(
                                "queue",
                                "Incomplete ingestion prevents safe duplicate detection; "
                                "restore access and rerun before routing.",
                            )
                            self.save(record)
                            continue
                        extraction = record.extraction
                        assert extraction is not None
                        extra = reviews.get(record.fax.fax_id)
                        matched_id = duplicates.get(record.fax.fax_id)
                        if extraction.resend_evidence and not matched_id:
                            extra = (
                                extra or "Possible resend without a confirmed matching original."
                            )
                        record.duplicate_of = None
                        if matched_id:
                            matched = by_id[matched_id]
                            # A matching transmission is skipped only for a UI-verified saved route.
                            if matched.verification == "verified" and matched.observed is not None:
                                outcome = self.verify(browser, record)
                                if outcome == "routed":
                                    continue
                                if outcome != "new":
                                    record.action = "failed"
                                    record.verification = "failed"
                                    self.save(record)
                                    continue
                                record.duplicate_of = matched_id
                                record.action = "skipped_duplicate"
                                record.selected = None
                                record.routing_note = None
                                record.submission_pending = False
                                record.verification = "verified"
                                record.verified_at = timestamp()
                                record.event(
                                    "duplicate",
                                    "Matching source ID, patient name, DOB and "
                                    f"category; matching fax {matched_id} verified routed.",
                                )
                                self.save(record)
                                continue
                            extra = f"Matching fax {matched_id} has no verified completed route."
                        planned = ROUTES["Needs Review" if extra else extraction.classification]
                        self.route(browser, record, planned, routing_note(extraction, extra))
                        log.info(
                            "%s: %s (%s)", record.fax.fax_id, record.action, record.verification
                        )
                finally:
                    browser.close()
            self.success = complete and all(r.verification == "verified" for r in self.records)
            status = "complete" if self.success else "failed"
            return self.store.export(self.records, status)
        except BaseException as exc:
            status = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
            self.store.export(self.records, status)
            raise
