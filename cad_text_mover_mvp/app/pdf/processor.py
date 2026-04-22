from __future__ import annotations

import json
import math
from pathlib import Path

import fitz

from app.config import Settings
from app.pdf.geometry import PageGeometryExtractor
from app.pdf.placement import MarginPacker
from app.pdf.scoring import TextDecisionEngine
from app.pdf.text_extraction import TextExtractor
from app.pdf.types import MoveRecord, PageAudit, Placement, ProcessingAudit, RectBox, TextItem


class CadPdfProcessor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.geometry_extractor = PageGeometryExtractor(
            margin_density_threshold=settings.margin_density_threshold,
            min_margin_points=settings.min_margin_size_points,
        )
        self.text_extractor = TextExtractor(
            min_native_items_for_no_ocr=settings.ocr_fallback_min_native_items,
        )
        self.decision_engine = TextDecisionEngine(
            overlap_move_threshold=settings.overlap_move_threshold,
            overlap_review_threshold=settings.overlap_review_threshold,
            max_rotation_degrees=settings.max_rotation_degrees,
            max_relocate_chars=settings.max_relocate_chars,
        )

    def process_pdf(self, *, input_pdf: Path, output_pdf: Path, audit_json: Path) -> ProcessingAudit:
        doc = fitz.open(str(input_pdf))
        page_audits: list[PageAudit] = []
        moved_total = 0
        review_total = 0
        ocr_pages = 0

        for page_index in range(doc.page_count):
            page = doc[page_index]
            page_rect = RectBox.from_fitz(page.rect)
            image_rgb, scale_x, scale_y = self.geometry_extractor.render_page(page, dpi=self.settings.render_dpi)
            text_items, used_ocr = self.text_extractor.extract_text_items(
                page=page,
                page_number=page_index,
                image_rgb=image_rgb,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            if used_ocr:
                ocr_pages += 1
            geometry = self.geometry_extractor.extract(
                page=page,
                image_rgb=image_rgb,
                text_items=text_items,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            annotated_items = self.decision_engine.annotate_items(text_items, geometry)
            page_audit = PageAudit(
                page_number=page_index,
                page_width=page_rect.width,
                page_height=page_rect.height,
                native_text_items=sum(1 for item in annotated_items if item.extraction_method == "native"),
                ocr_text_items=sum(1 for item in annotated_items if item.extraction_method == "ocr"),
                drawing_component_count=geometry.drawing_component_count,
                margin_strips={name: rect.to_dict() for name, rect in geometry.margin_strips.items()},
            )
            if used_ocr:
                page_audit.review_flags.append("ocr_fallback_used")
            if not geometry.margin_strips:
                page_audit.review_flags.append("no_margin_strip_detected")

            move_candidates = [item for item in annotated_items if item.classification == "move"]
            blockers = [
                item.bbox.expanded(1.0)
                for item in annotated_items
                if item.classification != "move"
            ]
            packer = MarginPacker(geometry)
            placements: dict[str, Placement] = {}

            for item in sorted(
                move_candidates,
                key=lambda x: (x.overlap_score, x.bbox.area),
                reverse=True,
            ):
                placement = packer.place(item, blockers)
                if placement is None:
                    item.classification = "review"
                    item.review_flags.append("no_margin_slot")
                    page_audit.move_records.append(
                        MoveRecord(
                            page_number=page_index,
                            text_item=item,
                            placement=None,
                            final_confidence=self.decision_engine.final_confidence(item, None),
                            moved=False,
                            redaction_strategy="none",
                        )
                    )
                    continue

                placements[item.item_id] = placement
                blockers.append(placement.target_bbox.expanded(2.0))
                final_confidence = self.decision_engine.final_confidence(item, placement)
                if final_confidence < 0.65:
                    item.review_flags.append("manual_review_recommended")
                page_audit.move_records.append(
                    MoveRecord(
                        page_number=page_index,
                        text_item=item,
                        placement=placement,
                        final_confidence=final_confidence,
                        moved=True,
                        redaction_strategy=(
                            "native_redaction"
                            if item.extraction_method == "native"
                            else "visual_whiteout"
                        ),
                    )
                )
                moved_total += 1

            review_item_ids = {item.item_id for item in annotated_items if item.classification == "review"}
            review_item_ids.update(
                record.text_item.item_id
                for record in page_audit.move_records
                if "manual_review_recommended" in record.text_item.review_flags
            )
            review_total += len(review_item_ids)

            self._apply_page_updates(page, page_audit.move_records)
            page_audits.append(page_audit)

        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_pdf), garbage=4, deflate=True)
        doc.close()

        audit = ProcessingAudit(
            input_pdf=str(input_pdf),
            output_pdf=str(output_pdf),
            pages=page_audits,
            summary={
                "pages": len(page_audits),
                "moved_text_count": moved_total,
                "review_item_count": review_total,
                "ocr_pages": ocr_pages,
            },
        )
        audit_json.parent.mkdir(parents=True, exist_ok=True)
        audit_json.write_text(json.dumps(audit.to_dict(), indent=2), encoding="utf-8")
        return audit

    def _apply_page_updates(self, page: fitz.Page, move_records: list[MoveRecord]) -> None:
        native_moves = [record for record in move_records if record.moved and record.text_item.extraction_method == "native"]
        ocr_moves = [record for record in move_records if record.moved and record.text_item.extraction_method == "ocr"]

        for record in native_moves:
            page.add_redact_annot(
                record.text_item.bbox.to_fitz(),
                fill=False,
                cross_out=False,
            )

        if native_moves:
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )

        for record in ocr_moves:
            whiteout = record.text_item.bbox.expanded(0.75).to_fitz()
            page.draw_rect(whiteout, color=None, fill=(1, 1, 1), width=0, overlay=True)

        for record in [r for r in move_records if r.moved and r.placement is not None]:
            self._draw_callout(page, record)

    def _draw_callout(self, page: fitz.Page, record: MoveRecord) -> None:
        assert record.placement is not None
        target = record.placement.target_bbox.to_fitz()
        page.draw_rect(target, color=(0, 0, 0), fill=(1, 1, 1), width=0.6, overlay=True)
        self._insert_text(
            page,
            target,
            record.placement.render_text,
            record.placement.render_font_size,
        )
        self._draw_leader(page, record.text_item.bbox, record.placement.target_bbox)

    def _insert_text(self, page: fitz.Page, target: fitz.Rect, text: str, font_size: float) -> None:
        inner = fitz.Rect(target.x0 + 1.5, target.y0 + 1.5, target.x1 - 1.5, target.y1 - 1.5)
        fallback = fitz.Rect(target.x0 + 1.0, target.y0 + 1.0, target.x1 - 1.0, target.y1 - 1.0)
        font_size = max(5.0, font_size)
        while font_size >= 5.0:
            spare = page.insert_textbox(
                inner,
                text,
                fontname="helv",
                fontsize=font_size,
                color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_LEFT,
                overlay=True,
            )
            if spare >= 0:
                return
            font_size -= 0.5
        page.insert_textbox(
            fallback,
            text,
            fontname="helv",
            fontsize=5.0,
            color=(0, 0, 0),
            align=fitz.TEXT_ALIGN_LEFT,
            overlay=True,
        )

    def _draw_leader(self, page: fitz.Page, source: RectBox, target: RectBox) -> None:
        start = source.center
        end = self._nearest_point_on_rect(target, start)
        page.draw_line(start, end, color=(0, 0, 0), width=0.5, overlay=True)

    def _nearest_point_on_rect(self, rect: RectBox, source_point: tuple[float, float]) -> tuple[float, float]:
        x, y = source_point
        cx, cy = rect.center
        dx = x - cx
        dy = y - cy
        if abs(dx) > abs(dy):
            return (rect.x0 if dx > 0 else rect.x1, min(max(y, rect.y0), rect.y1))
        return (min(max(x, rect.x0), rect.x1), rect.y0 if dy > 0 else rect.y1)
