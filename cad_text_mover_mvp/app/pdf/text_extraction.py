from __future__ import annotations

import math
import os
from collections import defaultdict
from statistics import median
from typing import Any

import fitz
import numpy as np
import pytesseract
from pytesseract import Output

_tesseract_cmd = os.getenv("TESSERACT_CMD", "").strip()
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd

from app.pdf.types import RectBox, TextItem


class TextExtractor:
    def __init__(self, min_native_items_for_no_ocr: int = 1) -> None:
        self.min_native_items_for_no_ocr = min_native_items_for_no_ocr

    def extract_text_items(
        self,
        *,
        page: fitz.Page,
        page_number: int,
        image_rgb: np.ndarray,
        scale_x: float,
        scale_y: float,
    ) -> tuple[list[TextItem], bool]:
        native_items = self._extract_native_items(page=page, page_number=page_number)
        if len(native_items) >= self.min_native_items_for_no_ocr:
            return native_items, False
        ocr_items = self._extract_ocr_items(
            page_number=page_number,
            image_rgb=image_rgb,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        return ocr_items, True

    def _extract_native_items(self, *, page: fitz.Page, page_number: int) -> list[TextItem]:
        text = page.get_text("rawdict", sort=True)
        items: list[TextItem] = []
        line_index = 0
        for block_index, block in enumerate(text.get("blocks", [])):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                raw_parts: list[str] = []
                sizes: list[float] = []
                fonts: list[str] = []
                for span in spans:
                    if "text" in span:
                        span_text = str(span.get("text") or "")
                    else:
                        span_text = "".join(ch.get("c", "") for ch in span.get("chars", []))
                    if span_text:
                        raw_parts.append(span_text)
                    try:
                        sizes.append(float(span.get("size") or 0.0))
                    except (TypeError, ValueError):
                        pass
                    font_name = span.get("font")
                    if font_name:
                        fonts.append(str(font_name))
                line_text = "".join(raw_parts).strip()
                if not line_text:
                    continue
                direction = line.get("dir") or (1.0, 0.0)
                angle_degrees = math.degrees(math.atan2(float(direction[1]), float(direction[0])))
                bbox = RectBox.from_tuple(line["bbox"])
                item = TextItem(
                    item_id=f"p{page_number}-native-{line_index}",
                    page_number=page_number,
                    text=line_text,
                    bbox=bbox,
                    font_size=float(median(sizes)) if sizes else max(6.0, bbox.height * 0.8),
                    angle_degrees=angle_degrees,
                    extraction_method="native",
                    extraction_confidence=1.0,
                    metadata={
                        "block_index": block_index,
                        "fonts": sorted(set(fonts)),
                    },
                )
                items.append(item)
                line_index += 1
        return items

    def _extract_ocr_items(
        self,
        *,
        page_number: int,
        image_rgb: np.ndarray,
        scale_x: float,
        scale_y: float,
    ) -> list[TextItem]:
        ocr = pytesseract.image_to_data(
            image_rgb,
            output_type=Output.DICT,
            config="--oem 3 --psm 11",
        )
        groups: dict[tuple[int, int, int], list[int]] = defaultdict(list)
        for idx, text in enumerate(ocr.get("text", [])):
            content = str(text or "").strip()
            conf = self._safe_float(ocr.get("conf", [])[idx] if idx < len(ocr.get("conf", [])) else -1)
            if not content or conf < 0:
                continue
            key = (
                int(self._safe_float(ocr.get("block_num", [])[idx] if idx < len(ocr.get("block_num", [])) else 0)),
                int(self._safe_float(ocr.get("par_num", [])[idx] if idx < len(ocr.get("par_num", [])) else 0)),
                int(self._safe_float(ocr.get("line_num", [])[idx] if idx < len(ocr.get("line_num", [])) else 0)),
            )
            groups[key].append(idx)

        items: list[TextItem] = []
        for seq, (_, indexes) in enumerate(sorted(groups.items(), key=lambda kv: kv[0])):
            words = [str(ocr["text"][idx]).strip() for idx in indexes if str(ocr["text"][idx]).strip()]
            if not words:
                continue
            left = min(int(ocr["left"][idx]) for idx in indexes)
            top = min(int(ocr["top"][idx]) for idx in indexes)
            right = max(int(ocr["left"][idx]) + int(ocr["width"][idx]) for idx in indexes)
            bottom = max(int(ocr["top"][idx]) + int(ocr["height"][idx]) for idx in indexes)
            conf_values = [self._safe_float(ocr["conf"][idx]) for idx in indexes]
            confidence = max(0.0, min(1.0, (sum(conf_values) / max(len(conf_values), 1)) / 100.0))
            bbox = RectBox(
                left / scale_x,
                top / scale_y,
                right / scale_x,
                bottom / scale_y,
            )
            items.append(
                TextItem(
                    item_id=f"p{page_number}-ocr-{seq}",
                    page_number=page_number,
                    text=" ".join(words),
                    bbox=bbox,
                    font_size=max(6.0, bbox.height * 0.8),
                    angle_degrees=0.0,
                    extraction_method="ocr",
                    extraction_confidence=confidence,
                    metadata={"ocr_word_count": len(words)},
                )
            )
        return items

    def _safe_float(self, raw: Any) -> float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0
