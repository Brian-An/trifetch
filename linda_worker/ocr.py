from __future__ import annotations

import csv
import hashlib
import io
import math
from contextlib import closing
from pathlib import Path
from typing import cast

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageOps

from linda_worker.models import Line, PageText

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PAGES = 100
MAX_PIXELS = 16_000_000


class InvalidPDF(ValueError):
    pass


def validate_pdf(path: Path) -> str:
    if not 0 < path.stat().st_size <= MAX_PDF_BYTES:
        raise InvalidPDF("PDF is empty or exceeds the 25 MiB download limit.")
    with path.open("rb") as stream:
        if not stream.read(5) == b"%PDF-":
            raise InvalidPDF("Download is not a PDF (possibly an HTML error or login page).")
    try:
        with pdfium.PdfDocument(path) as document:
            if not 0 < len(document) <= MAX_PAGES:
                raise InvalidPDF("PDF must contain between 1 and 100 pages.")
    except pdfium.PdfiumError as exc:
        raise InvalidPDF(f"PDF cannot be opened: {exc}") from exc
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_ocr() -> str:
    try:
        version = str(pytesseract.get_tesseract_version())
        if "eng" not in pytesseract.get_languages(config=""):
            raise RuntimeError("Tesseract English language data is missing.")
        return f"Tesseract {version}; English; policy v1"
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Install Tesseract OCR with English data and put tesseract on PATH."
        ) from exc


def read_image(image: Image.Image, number: int, method: str, psm: int) -> PageText:
    # pytesseract has an untyped return boundary; TSV keeps the parsed values explicit.
    tsv = cast(str, pytesseract.image_to_data(image, lang="eng", config=f"--psm {psm}", timeout=30))
    groups: dict[tuple[str, str, str], list[tuple[str, float]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = row.get("text", "").strip()
        confidence = max(0.0, min(100.0, float(row.get("conf", "-1"))))
        if text:
            key = (row["block_num"], row["par_num"], row["line_num"])
            groups.setdefault(key, []).append((text, confidence))
    lines = []
    for words in groups.values():
        total = sum(len(text) for text, _ in words)
        confidence = sum(len(text) * conf for text, conf in words) / total
        lines.append(Line(text=" ".join(text for text, _ in words), confidence=confidence))
    total = sum(len(line.text) for line in lines)
    confidence = sum(len(line.text) * line.confidence for line in lines) / total if total else 0
    return PageText(
        number=number,
        text="\n".join(line.text for line in lines),
        confidence=confidence,
        lines=lines,
        method=method,
    )


def readable(page: PageText) -> bool:
    return page.confidence >= 85 and len(page.text.split()) >= 12


def extract_pages(path: Path) -> list[PageText]:
    validate_pdf(path)
    pages = []
    with pdfium.PdfDocument(path) as document:
        for index in range(len(document)):
            number = index + 1
            try:
                with closing(document[index]) as page:
                    width, height = page.get_size()
                    if width <= 0 or height <= 0:
                        raise ValueError("Invalid page dimensions.")
                    scale = min(240 / 72, math.sqrt(MAX_PIXELS / (width * height)))
                    with closing(page.render(scale=scale, grayscale=True)) as bitmap:
                        image = bitmap.to_pil().copy()
                with image:
                    attempts = []
                    failures = []
                    for angle, psm in ((0, 3), (0, 6), (90, 3), (180, 3), (270, 3)):
                        with ImageOps.autocontrast(image.rotate(angle, expand=True)) as candidate:
                            try:
                                attempt = read_image(
                                    candidate, number, f"rotation={angle};psm={psm}", psm
                                )
                            except (RuntimeError, pytesseract.TesseractError) as exc:
                                failures.append(str(exc))
                                continue
                        attempts.append(attempt)
                        if readable(attempt):
                            break
                    if not attempts:
                        raise RuntimeError("All OCR passes failed: " + "; ".join(failures))
                    best = max(attempts, key=lambda p: (readable(p), p.confidence, len(p.text)))
                    if not readable(best):
                        best.warnings.append(
                            "OCR remains unreliable after layout/rotation retries."
                        )
                    pages.append(best)
            except (RuntimeError, ValueError, pdfium.PdfiumError) as exc:
                pages.append(
                    PageText(
                        number=number,
                        text="",
                        confidence=0,
                        method="failed",
                        warnings=[f"Page could not be read: {exc}"],
                    )
                )
    return pages
