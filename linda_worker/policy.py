from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime

from linda_worker.models import Category, Evidence, Extraction, PageText, Record, Route

ROUTES: dict[Category, Route] = {
    "Referral": Route(
        assigned_to="Olivia Chen - Referrals",
        document_type="Referral",
        document_folder="Referrals / New",
    ),
    "Lab Result": Route(
        assigned_to="Noah Patel - Clinical Review",
        document_type="Lab Result",
        document_folder="Clinical / Labs",
    ),
    "Imaging Report": Route(
        assigned_to="Noah Patel - Clinical Review",
        document_type="Imaging Report",
        document_folder="Clinical / Imaging",
    ),
    "Prior Authorization": Route(
        assigned_to="Ethan Brooks - Authorizations",
        document_type="Prior Authorization",
        document_folder="Authorizations / Pending",
    ),
    "Refill Request": Route(
        assigned_to="Ava Wilson - Nursing / Medication",
        document_type="Refill Request",
        document_folder="Nursing / Medication",
    ),
    "Records Request": Route(
        assigned_to="Mia Gomez - Medical Records",
        document_type="Records Request",
        document_folder="Records / Requests",
    ),
    "EOB": Route(
        assigned_to="Liam Rivera - Billing", document_type="EOB", document_folder="Billing / EOBs"
    ),
    "Needs Review": Route(
        assigned_to="Jordan Ellis - Manual Review",
        document_type="Needs Review",
        document_folder="Review / Unclassified",
    ),
}

# Category evidence comes from document headings, not incidental clinical prose.
HEADINGS: dict[Category, re.Pattern[str]] = {
    "Referral": re.compile(
        r"^(?:fax resend\s*[-:]\s*)?(?:new patient\s+)?referral(?: request)?$|"
        r"^(?:specialist\s+)?consult(?:ation)? request$",
        re.I,
    ),
    "Lab Result": re.compile(
        r"^(?:routine\s+)?(?:laboratory|lab)(?: test)? "
        r"(?:results?|report)(?:\s*\(.*\))?$",
        re.I,
    ),
    "Imaging Report": re.compile(
        r"^(?:(?:final|diagnostic)\s+)?(?:imaging|radiology|radiological) "
        r"report$",
        re.I,
    ),
    "Prior Authorization": re.compile(
        r"^(?:request for )?prior authori[sz]ation(?: request)?$", re.I
    ),
    "Refill Request": re.compile(
        r"^(?:(?:pharmacy|prescription|medication)\s+)?"
        r"refill request$",
        re.I,
    ),
    "Records Request": re.compile(
        r"^(?:request for (?:release (?:of )?|copy of )?medical records|"
        r"(?:medical )?records request|"
        r"authori[sz]ation to release information)$",
        re.I,
    ),
    "EOB": re.compile(
        r"^(?:explanation of benefits(?:\s*\(eob\))?|eob|"
        r"(?:insurance |insurer )?remittance(?: advice)?)$",
        re.I,
    ),
}
SOURCE = re.compile(
    r"^(?:original\s+)?(?:document\s+(?:id|reference|number)|report\s+id|"
    r"(?:records\s+)?request\s+(?:id|number)|accession(?:\s+number)?|"
    r"remittance\s+id|related\s+request)\s*:\s*([A-Z0-9][A-Z0-9._/-]*)\s*$",
    re.I,
)
NAME = re.compile(r"^(?:patient(?: name)?|member(?: name)?)\s*:\s*(.+)$", re.I)
DOB = re.compile(r"^(?:date of birth|dob)\s*:\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})\s*$", re.I)
URGENT = re.compile(r"\b(?:urgent|urgently|critical|stat|emergency)\b", re.I)
NEGATED = re.compile(
    r"\b(?:no|not|non)[ -](?:urgent|critical|emergency)\b|"
    r"\b(?:urgent|critical)\s*:\s*(?:no|false|none)\b",
    re.I,
)
INCOMPLETE = re.compile(
    r"\b(?:missing\s+(?:\w+\s+)?page|page\s+\d+\s+(?:is\s+)?missing|"
    r"incomplete|illegible|unreadable)\b",
    re.I,
)


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def values(evidence: list[Evidence]) -> set[str]:
    return {normalized(item.value) for item in evidence if item.confidence >= 80}


def source_values(evidence: list[Evidence]) -> set[str]:
    return {item.value.strip().upper() for item in evidence if item.confidence >= 80}


def classify(pages: list[PageText], expected_pages: int, engine: str) -> Extraction:
    result = Extraction(engine=engine, pages=pages, classification="Needs Review")
    if len(pages) != expected_pages:
        result.uncertainties.append(
            f"Portal declares {expected_pages} pages; PDF has {len(pages)}."
        )
    for page in pages:
        result.uncertainties.extend(f"Page {page.number}: {w}" for w in page.warnings)
        if page.confidence < 80 or len(re.findall(r"\w+", page.text)) < 12:
            result.uncertainties.append(f"Page {page.number}: insufficient reliable OCR evidence.")
        category_on_page = False
        for line in page.lines:
            text = line.text.strip().strip(" |")
            evidence = Evidence(
                value=text, page=page.number, quote=text, confidence=line.confidence
            )
            for category, pattern in HEADINGS.items():
                if pattern.fullmatch(text):
                    category_on_page = True
                    if line.confidence < 80:
                        result.uncertainties.append(f"Page {page.number}: low-confidence heading.")
                    if category not in result.categories:
                        result.categories.append(category)
                    result.evidence.append(evidence.model_copy(update={"value": category}))
            for pattern, target in (
                (SOURCE, result.source_ids),
                (NAME, result.patient_names),
                (DOB, result.dates_of_birth),
            ):
                match = pattern.fullmatch(text)
                if match:
                    target.append(evidence.model_copy(update={"value": match[1].strip()}))
                    if pattern is SOURCE and line.confidence < 80:
                        result.uncertainties.append(
                            f"Page {page.number}: source identifier has low OCR confidence."
                        )
                    if pattern is DOB:
                        month, day, year = map(int, re.split(r"[/.-]", match[1]))
                        try:
                            date(year, month, day)
                        except ValueError:
                            result.uncertainties.append(
                                f"Page {page.number}: DOB is not a valid visible calendar date."
                            )
            if re.search(r"\b(?:resend|resent|retransmission|duplicate copy)\b", text, re.I):
                result.resend_evidence.append(evidence)
            # Remove only negated occurrences, preserving a second positive flag on the same line.
            if URGENT.search(NEGATED.sub("", text)):
                result.flags.append(f"Page {page.number}: {text}")
            if INCOMPLETE.search(text):
                result.uncertainties.append(f"Page {page.number}: {text}")
            declared = re.search(r"number of pages.*?:\s*(\d+)", text, re.I)
            if declared and int(declared[1]) != len(pages):
                result.uncertainties.append(
                    f"Page {page.number}: cover sheet page count disagrees."
                )
            footer = re.search(r"\bpage\s*([\dIl]+)\s*of\s*([\dIl]+)\b", text, re.I)
            if footer:
                number, total = (
                    int(s.translate(str.maketrans("Il", "11"))) for s in footer.groups()
                )
                if number != page.number or total != len(pages):
                    result.uncertainties.append(
                        f"Page {page.number}: printed page sequence disagrees."
                    )
        if not category_on_page:
            is_cover = bool(re.search(r"\bfax cover\b", page.text, re.I))
            if not is_cover:
                result.uncertainties.append(f"Page {page.number}: no recognized document heading.")
    if len(result.categories) > 1:
        result.uncertainties.append("Mixed packet: " + ", ".join(result.categories) + ".")
    if len(values(result.patient_names)) > 1 or len(values(result.dates_of_birth)) > 1:
        result.uncertainties.append("Conflicting patient identifiers within packet.")
    if not result.categories:
        result.uncertainties.append(
            "Document category cannot be established from scanned evidence."
        )
    if len(result.categories) == 1 and not result.uncertainties and not result.flags:
        result.classification = result.categories[0]
    result.flags = list(dict.fromkeys(result.flags))
    result.uncertainties = list(dict.fromkeys(result.uncertainties))
    return result


def received_key(record: Record) -> tuple[datetime, str]:
    raw = record.fax.received_at.replace(" UTC", "+00:00").replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    return parsed, record.fax.fax_id


def duplicate_plan(records: list[Record]) -> tuple[dict[str, str], dict[str, str]]:
    """Require a source identifier, matching patient context, and unambiguous chronology."""
    groups: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        if record.extraction and len(source_values(record.extraction.source_ids)) == 1:
            groups[next(iter(source_values(record.extraction.source_ids)))].append(record)
    duplicates: dict[str, str] = {}
    reviews: dict[str, str] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        contexts = []
        for record in group:
            extraction = record.extraction
            assert extraction is not None
            names, dobs = values(extraction.patient_names), values(extraction.dates_of_birth)
            contexts.append((names, dobs, set(extraction.categories)))
        reliable = all(len(n) == len(d) == len(c) == 1 for n, d, c in contexts)
        reliable = reliable and all(
            r.extraction and not r.extraction.flags and not r.extraction.uncertainties
            for r in group
        )
        if not reliable or any(context != contexts[0] for context in contexts[1:]):
            for record in group:
                reviews[record.fax.fax_id] = (
                    "Source ID repeats without matching reliable patient context."
                )
            continue
        completed = [
            record
            for record in group
            if record.fax.status == "Routed"
            and record.observed is not None
            and record.verification == "verified"
        ]
        if completed:
            # Completed work takes precedence over chronology, including a later resend.
            anchor = min(completed, key=lambda record: record.fax.fax_id)
            for record in group:
                if record.fax.status == "New":
                    duplicates[record.fax.fax_id] = anchor.fax.fax_id
            continue
        try:
            ordered = sorted(group, key=received_key)
            earliest_time = received_key(ordered[0])[0]
            tied = sum(received_key(r)[0] == earliest_time for r in ordered) > 1
        except (ValueError, TypeError):
            tied = True
        if tied:
            for record in group:
                reviews[record.fax.fax_id] = (
                    "Cannot determine earliest original from received times."
                )
            continue
        for record in ordered[1:]:
            duplicates[record.fax.fax_id] = ordered[0].fax.fax_id
    return duplicates, reviews


def routing_note(extraction: Extraction, extra: str | None = None) -> str:
    reasons = extraction.flags + extraction.uncertainties + ([extra] if extra else [])
    if reasons:
        return "; ".join(reasons)[:1000]
    source = ", ".join(dict.fromkeys(item.value for item in extraction.source_ids))
    return (
        f"OCR evidence: {extraction.classification}. Source reference: {source or 'not visible'}."
    )
