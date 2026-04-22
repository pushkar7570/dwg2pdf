from __future__ import annotations

from pathlib import Path

import fitz

from app.config import get_settings
from app.pdf.processor import CadPdfProcessor


def build_sample_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    drawing_rect = fitz.Rect(100, 80, 450, 300)
    page.draw_rect(drawing_rect, color=(0, 0, 0), width=1.0)
    page.draw_line((120, 100), (430, 280), color=(0, 0, 0), width=1.0)
    page.draw_line((120, 280), (430, 100), color=(0, 0, 0), width=1.0)
    page.draw_line((100, 190), (450, 190), color=(0, 0, 0), width=1.0)
    page.insert_text((200, 190), "VALVE A", fontname="helv", fontsize=12)
    page.insert_text((20, 30), "SHEET A1", fontname="helv", fontsize=14)
    doc.save(path)
    doc.close()


def test_processor_moves_overlapping_text_to_margin(tmp_path: Path) -> None:
    settings = get_settings()
    processor = CadPdfProcessor(settings)
    input_pdf = tmp_path / "input.pdf"
    output_pdf = tmp_path / "output.pdf"
    audit_json = tmp_path / "audit.json"
    build_sample_pdf(input_pdf)

    audit = processor.process_pdf(input_pdf=input_pdf, output_pdf=output_pdf, audit_json=audit_json)

    assert output_pdf.exists()
    assert audit_json.exists()
    assert audit.summary["moved_text_count"] >= 1

    first_page = audit.pages[0]
    moved_records = [record for record in first_page.move_records if record.moved]
    assert moved_records, "Expected at least one moved text item"
    target = moved_records[0].placement.target_bbox  # type: ignore[union-attr]
    assert target.x1 <= 100 or target.x0 >= 450 or target.y1 <= 80 or target.y0 >= 300
