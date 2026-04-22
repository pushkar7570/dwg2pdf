from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import fitz
from shapely.geometry import Point, box


@dataclass(frozen=True)
class RectBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_fitz(cls, rect: fitz.Rect) -> "RectBox":
        return cls(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))

    @classmethod
    def from_tuple(cls, raw: tuple[float, float, float, float] | list[float]) -> "RectBox":
        x0, y0, x1, y1 = raw
        return cls(float(x0), float(y0), float(x1), float(y1))

    def to_fitz(self) -> fitz.Rect:
        return fitz.Rect(self.x0, self.y0, self.x1, self.y1)

    def to_polygon(self):
        return box(self.x0, self.y0, self.x1, self.y1)

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    def expanded(self, pad_x: float, pad_y: float | None = None) -> "RectBox":
        pad_y = pad_x if pad_y is None else pad_y
        return RectBox(
            self.x0 - pad_x,
            self.y0 - pad_y,
            self.x1 + pad_x,
            self.y1 + pad_y,
        )

    def inset(self, pad: float) -> "RectBox":
        return RectBox(self.x0 + pad, self.y0 + pad, self.x1 - pad, self.y1 - pad)

    def clamp(self, outer: "RectBox") -> "RectBox":
        return RectBox(
            max(outer.x0, self.x0),
            max(outer.y0, self.y0),
            min(outer.x1, self.x1),
            min(outer.y1, self.y1),
        )

    def intersects(self, other: "RectBox") -> bool:
        return not (
            self.x1 <= other.x0
            or self.x0 >= other.x1
            or self.y1 <= other.y0
            or self.y0 >= other.y1
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def contains_rect(self, other: "RectBox") -> bool:
        return (
            self.x0 <= other.x0
            and self.y0 <= other.y0
            and self.x1 >= other.x1
            and self.y1 >= other.y1
        )

    def distance_to_point(self, x: float, y: float) -> float:
        cx, cy = self.center
        return math.hypot(cx - x, cy - y)

    def to_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


@dataclass
class TextItem:
    item_id: str
    page_number: int
    text: str
    bbox: RectBox
    font_size: float
    angle_degrees: float
    extraction_method: str
    extraction_confidence: float
    classification: str = "keep"
    overlap_score: float = 0.0
    overlap_metrics: dict[str, float] = field(default_factory=dict)
    review_flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["bbox"] = self.bbox.to_dict()
        return payload


@dataclass
class Placement:
    strip_name: str
    target_bbox: RectBox
    distance: float
    strip_score: float
    placement_confidence: float
    render_text: str
    render_font_size: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "strip_name": self.strip_name,
            "target_bbox": self.target_bbox.to_dict(),
            "distance": self.distance,
            "strip_score": self.strip_score,
            "placement_confidence": self.placement_confidence,
            "render_text": self.render_text,
            "render_font_size": self.render_font_size,
        }


@dataclass
class MoveRecord:
    page_number: int
    text_item: TextItem
    placement: Placement | None
    final_confidence: float
    moved: bool
    redaction_strategy: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "moved": self.moved,
            "final_confidence": self.final_confidence,
            "redaction_strategy": self.redaction_strategy,
            "text_item": self.text_item.to_dict(),
            "placement": self.placement.to_dict() if self.placement else None,
        }


@dataclass
class PageAudit:
    page_number: int
    page_width: float
    page_height: float
    native_text_items: int
    ocr_text_items: int
    drawing_component_count: int
    margin_strips: dict[str, dict[str, float]]
    move_records: list[MoveRecord] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "native_text_items": self.native_text_items,
            "ocr_text_items": self.ocr_text_items,
            "drawing_component_count": self.drawing_component_count,
            "margin_strips": self.margin_strips,
            "move_records": [record.to_dict() for record in self.move_records],
            "review_flags": list(self.review_flags),
        }


@dataclass
class ProcessingAudit:
    input_pdf: str
    output_pdf: str
    pages: list[PageAudit]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_pdf": self.input_pdf,
            "output_pdf": self.output_pdf,
            "pages": [page.to_dict() for page in self.pages],
            "summary": self.summary,
        }
