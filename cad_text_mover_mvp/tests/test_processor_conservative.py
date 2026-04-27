from __future__ import annotations

from pathlib import Path

import fitz
import numpy as np
from shapely.geometry import box

from app.config import get_settings
from app.pdf.geometry import PageGeometry
from app.pdf.processor import CadPdfProcessor
from app.pdf.types import RectBox, TextItem


def test_processor_leaves_ocr_only_pages_unmodified(monkeypatch, tmp_path: Path) -> None:
    settings = get_settings()
    processor = CadPdfProcessor(settings)
    input_pdf = tmp_path / "input.pdf"
    output_pdf = tmp_path / "output.pdf"
    audit_json = tmp_path / "audit.json"

    doc = fitz.open()
    doc.new_page(width=200, height=200)
    doc.save(input_pdf)
    doc.close()

    image_rgb = np.full((200, 200, 3), 255, dtype=np.uint8)
    mask = np.zeros((200, 200), dtype=np.uint8)
    ocr_item = TextItem(
        item_id="p0-ocr-0",
        page_number=0,
        text="OCR LABEL",
        bbox=RectBox(50, 50, 115, 65),
        font_size=10.0,
        angle_degrees=0.0,
        extraction_method="ocr",
        extraction_confidence=0.95,
    )
    geometry = PageGeometry(
        page_rect=RectBox(0, 0, 200, 200),
        image_rgb=image_rgb,
        content_mask=mask,
        drawing_mask=mask,
        scale_x=1.0,
        scale_y=1.0,
        contour_geometries=[],
        vector_geometries=[],
        drawing_union=box(45, 45, 120, 70),
        margin_strips={"right": RectBox(150, 0, 200, 200)},
    )

    monkeypatch.setattr(
        processor.geometry_extractor,
        "render_page",
        lambda page, dpi: (image_rgb, 1.0, 1.0),
    )
    monkeypatch.setattr(
        processor.text_extractor,
        "extract_text_items",
        lambda **kwargs: ([ocr_item], True),
    )
    monkeypatch.setattr(
        processor.geometry_extractor,
        "extract",
        lambda **kwargs: geometry,
    )

    audit = processor.process_pdf(input_pdf=input_pdf, output_pdf=output_pdf, audit_json=audit_json)

    assert output_pdf.exists()
    assert audit_json.exists()
    assert audit.summary["moved_text_count"] == 0
    assert "page_left_unmodified_due_to_ocr" in audit.pages[0].review_flags
    assert any(record.text_item.item_id == "p0-ocr-0" and not record.moved for record in audit.pages[0].move_records)
