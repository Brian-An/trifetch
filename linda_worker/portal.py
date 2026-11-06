from __future__ import annotations

import re
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Browser, BrowserContext, Locator, Page

from linda_worker.models import Fax, Route


class PortalError(RuntimeError):
    pass


class SessionExpired(PortalError):
    pass


@dataclass(frozen=True)
class BrowserConfig:
    base_url: str
    username: str
    password: str = field(repr=False)
    timeout_ms: int = 10_000


def key_values(container: Locator) -> dict[str, str]:
    keys = container.locator("dt").all_text_contents()
    vals = container.locator("dd").all_text_contents()
    if len(keys) != len(vals):
        raise PortalError("Unexpected details layout: labels and values disagree.")
    return {key.strip(): value.strip() for key, value in zip(keys, vals, strict=True)}


class Session(AbstractContextManager["Session"]):
    def __init__(self, browser: Browser, config: BrowserConfig) -> None:
        self.config = config
        self.context_id = uuid4().hex
        self.context: BrowserContext = browser.new_context(
            accept_downloads=True, viewport={"width": 1440, "height": 1000}, service_workers="block"
        )
        self.context.set_default_timeout(config.timeout_ms)
        self.context.set_default_navigation_timeout(config.timeout_ms)
        self.page: Page = self.context.new_page()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *args: object) -> None:
        self.context.close()

    def heading(self, text: str) -> None:
        if self.page.get_by_label("Username", exact=True).is_visible():
            raise SessionExpired("Login expired; reauthentication is required.")
        self.page.get_by_role("heading", name=text, exact=True).wait_for()

    def authenticate(self) -> None:
        self.page.goto(self.config.base_url, wait_until="domcontentloaded")
        self.page.get_by_label("Username", exact=True).fill(self.config.username)
        self.page.get_by_label("Password", exact=True).fill(self.config.password)
        self.page.get_by_role("button", name="Sign in", exact=True).click()
        if self.page.get_by_role("alert").count():
            raise PortalError("Login failed; check LINDA_USERNAME and LINDA_PASSWORD.")
        self.heading("Fax Management Workspace")

    def inbox(self) -> None:
        nav = self.page.get_by_role("complementary")
        if self.page.get_by_label("Username", exact=True).is_visible():
            raise SessionExpired("Session expired during navigation.")
        nav.get_by_role("link", name="Fax Management", exact=True).click()
        self.heading("Fax Management")
        nav.get_by_role("link", name="Inbound Faxes", exact=True).click()
        self.heading("Inbound Faxes")

    def discover(self) -> list[Fax]:
        self.inbox()
        found: dict[str, Fax] = {}
        seen_pages: set[str] = set()
        expected_total: int | None = None
        for _ in range(1000):
            self.heading("Inbound Faxes")
            pager = self.page.locator(".pagination").inner_text()
            if pager in seen_pages:
                raise PortalError("Inbox pagination repeated; queue discovery is incomplete.")
            seen_pages.add(pager)
            count = re.search(r"(\d+) transmissions", pager)
            if count:
                total = int(count[1])
                if expected_total is not None and expected_total != total:
                    raise PortalError(
                        "Inbox changed during discovery; retry a consistent snapshot."
                    )
                expected_total = total
            table = self.page.get_by_role("table", name="Inbound faxes", exact=True)
            for row in table.locator("tbody tr").all():
                cells = row.locator("td").all_text_contents()
                if len(cells) == 1 and "No faxes" in cells[0]:
                    continue
                if len(cells) != 6:
                    raise PortalError("Unexpected inbox columns; refusing incomplete discovery.")
                fax = Fax.model_validate(
                    {
                        "fax_id": cells[0].strip(),
                        "received_at": cells[1].strip(),
                        "sender": cells[2].strip(),
                        "expected_pages": int(cells[3]),
                        "status": cells[4].strip(),
                    }
                )
                if fax.fax_id in found:
                    raise PortalError("Fax ID repeated across inbox pages; retry discovery.")
                found[fax.fax_id] = fax
            next_link = self.page.get_by_role("link", name="Next", exact=True)
            if not next_link.count():
                if expected_total is None or expected_total != len(found):
                    raise PortalError("Discovered fax count does not match the inbox total.")
                return list(found.values())
            next_link.click()
        raise PortalError("Inbox exceeds the 1000-page safety limit.")

    def open_fax(self, fax_id: str) -> tuple[Fax, Route | None]:
        self.inbox()
        self.page.get_by_label("Search fax ID or sender", exact=True).fill(fax_id)
        self.page.get_by_role("button", name="Filter", exact=True).click()
        self.heading("Inbound Faxes")
        table = self.page.get_by_role("table", name="Inbound faxes", exact=True)
        table.get_by_role("link", name=fax_id, exact=True).click()
        self.heading(f"Fax {fax_id}")
        info = key_values(
            self.page.get_by_role("heading", name="Transmission information", exact=True).locator(
                ".."
            )
        )
        if info.get("Fax ID") != fax_id:
            raise PortalError("Fax ID differs from requested transmission; refusing action.")
        fax = Fax.model_validate(
            {
                "fax_id": info["Fax ID"],
                "received_at": info["Received"],
                "sender": info["Sender"],
                "expected_pages": int(info["Pages"]),
                "status": info["Status"],
            }
        )
        route = None
        if fax.status == "Routed":
            details = key_values(
                self.page.get_by_role("heading", name="Document routing", exact=True).locator("..")
            )
            if details.get("Routing status") != "Completed":
                raise PortalError("Routed fax lacks a completed assignment in the UI.")
            route = Route.model_validate(
                {
                    "assigned_to": details["Assigned To"],
                    "document_type": details["Document Type"],
                    "document_folder": details["Document Folder"],
                }
            )
        return fax, route

    def download(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        try:
            with self.page.expect_download() as info:
                self.page.get_by_role("link", name="Download Fax PDF", exact=True).click()
            download = info.value
            failure = download.failure()
            if failure:
                raise PortalError(f"Browser download failed: {failure}")
            download.save_as(temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def prepare_route(self, fax_id: str, route: Route, note: str) -> None:
        self.page.get_by_role("link", name="Route / Assign Fax", exact=True).click()
        self.heading(f"Route fax {fax_id}")
        source = key_values(
            self.page.get_by_role("heading", name="Source transmission", exact=True).locator("..")
        )
        if source.get("Fax ID") != fax_id:
            raise PortalError("Route form refers to a different fax ID.")
        for label, value in (
            ("Assigned To *", route.assigned_to),
            ("Document Type *", route.document_type),
            ("Document Folder *", route.document_folder),
        ):
            select = self.page.get_by_label(label, exact=True)
            select.select_option(label=value)
            if select.locator("option:checked").inner_text().strip() != value:
                raise PortalError(f"Dropdown {label} does not match the intended assignment.")
        self.page.get_by_label("Routing note (optional)", exact=True).fill(note)
        self.page.get_by_role("button", name="Review Routing", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Confirm document routing", exact=True)
        dialog.wait_for()
        details = key_values(dialog)
        if details != {
            "Fax": fax_id,
            "Assigned To": route.assigned_to,
            "Document Type": route.document_type,
            "Document Folder": route.document_folder,
        }:
            raise PortalError("Confirmation dialog differs from intended fax or assignment.")

    def submit(self) -> str:
        self.page.get_by_role("dialog", name="Confirm document routing", exact=True).get_by_role(
            "button", name="OK - Submit Routing", exact=True
        ).click()
        return "; ".join(self.page.get_by_role("alert").all_text_contents())

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=path, full_page=True)
