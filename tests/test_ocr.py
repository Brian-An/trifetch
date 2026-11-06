from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from linda_worker.ocr import InvalidPDF, extract_pages, validate_pdf
from linda_worker.policy import classify


@pytest.mark.parametrize("body", [b"", b"<html>503 Service Unavailable</html>", b"%PDF-broken"])
def test_invalid_downloads_are_never_treated_as_documents(tmp_path: Path, body: bytes) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(body)
    with pytest.raises(InvalidPDF):
        validate_pdf(path)


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_actual_ocr_reads_rasterized_rotated_pages(tmp_path: Path, angle: int) -> None:
    with Image.new("RGB", (1600, 2000), "white") as image:
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=42)
        draw.multiline_text(
            (80, 80),
            "LABORATORY REPORT\n\nDocument ID: LAB-1234\n"
            "Patient: Taylor Smith\nDOB: 01/01/1980\n"
            "Collection date: 11/01/2026\n\nStatus: Final\n"
            "Results enclosed for the receiving office.\n"
            "Routine report for administrative filing.\nPage 1 of 1",
            font=font,
            fill="black",
            spacing=20,
        )
        path = tmp_path / "scan.pdf"
        with image.rotate(angle, expand=True) as rotated:
            rotated.save(path, "PDF", resolution=200)
    pages = extract_pages(path)
    result = classify(pages, 1, "Tesseract")
    assert result.classification == "Lab Result", pages
    assert result.source_ids[0].value == "LAB-1234"
    assert result.patient_names[0].value == "Taylor Smith"
    assert result.dates_of_birth[0].value == "01/01/1980"


def test_blank_page_is_reviewed_instead_of_inventing_content(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    with Image.new("RGB", (600, 800), "white") as image:
        image.save(path, "PDF")
    result = classify(extract_pages(path), 1, "Tesseract")
    assert result.classification == "Needs Review"
    assert not result.source_ids
    assert result.uncertainties
