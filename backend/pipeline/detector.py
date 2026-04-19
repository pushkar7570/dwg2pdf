# backend/pipeline/detector.py

from shapely.geometry import box as shapely_box
from shapely.strtree import STRtree
from dataclasses import dataclass, field
from typing import List
from .parser import ParsedDocument, TextBlock, DrawingElement
from .utils import get_logger

logger = get_logger(__name__)

OVERLAP_THRESHOLD = 0.05   # 5% of text area overlapping = conflict


@dataclass
class OverlapConflict:
    """A single text block that overlaps with drawing elements"""
    text_block: TextBlock
    conflicting_drawings: List[DrawingElement] = field(default_factory=list)
    max_overlap_ratio: float = 0.0

    @property
    def text(self) -> str:
        return self.text_block.text

    @property
    def current_bbox(self) -> tuple:
        return self.text_block.bbox

    @property
    def page_no(self) -> int:
        return self.text_block.page_no

    def to_dict(self) -> dict:
        return {
            "text": self.text_block.text,
            "current_bbox": list(self.text_block.bbox),
            "page_no": self.text_block.page_no,
            "font_size": self.text_block.font_size,
            "font_name": self.text_block.font_name,
            "max_overlap_ratio": round(self.max_overlap_ratio, 3),
            "conflicting_drawing_bboxes": [
                list(d.bbox) for d in self.conflicting_drawings
            ]
        }


@dataclass
class DetectionResult:
    """Result of overlap detection for whole document"""
    has_overlaps: bool
    total_conflicts: int
    conflicts: List[OverlapConflict] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "has_overlaps": self.has_overlaps,
            "total_conflicts": self.total_conflicts,
            "summary": self.summary,
            "conflicts": [c.to_dict() for c in self.conflicts]
        }


def detect_overlaps(parsed_doc: ParsedDocument) -> DetectionResult:
    """
    Detect all text-drawing overlaps using Shapely spatial indexing.
    Uses STRtree for efficient spatial queries on large drawings.
    """
    logger.info("Running overlap detection...")
    all_conflicts: List[OverlapConflict] = []

    for page in parsed_doc.pages:
        if not page.text_blocks or not page.drawing_elements:
            continue

        page_conflicts = _detect_page_overlaps(page)
        all_conflicts.extend(page_conflicts)

    has_overlaps = len(all_conflicts) > 0
    summary = (
        f"Found {len(all_conflicts)} overlapping text block(s) across "
        f"{parsed_doc.page_count} page(s)."
        if has_overlaps
        else "No overlaps detected. Drawing is clean."
    )

    logger.info(summary)

    return DetectionResult(
        has_overlaps=has_overlaps,
        total_conflicts=len(all_conflicts),
        conflicts=all_conflicts,
        summary=summary
    )


def _detect_page_overlaps(page) -> List[OverlapConflict]:
    """Detect overlaps on a single page using spatial indexing"""
    conflicts = []

    # Build spatial index of drawing elements for fast lookup
    drawing_shapes = [shapely_box(*d.bbox) for d in page.drawing_elements]

    if not drawing_shapes:
        return conflicts

    # STRtree for O(log n) spatial queries
    spatial_index = STRtree(drawing_shapes)

    for text_block in page.text_blocks:
        text_shape = shapely_box(*text_block.bbox)
        text_area = text_shape.area

        if text_area < 1:  # Skip near-zero text
            continue

        # Query spatial index for candidates
        candidate_indices = spatial_index.query(text_shape)

        conflicting_drawings = []
        max_ratio = 0.0

        for idx in candidate_indices:
            drawing = page.drawing_elements[idx]
            draw_shape = drawing_shapes[idx]

            if not text_shape.intersects(draw_shape):
                continue

            try:
                intersection = text_shape.intersection(draw_shape)
                overlap_ratio = intersection.area / text_area
            except Exception:
                overlap_ratio = 0.0

            if overlap_ratio > OVERLAP_THRESHOLD:
                conflicting_drawings.append(drawing)
                max_ratio = max(max_ratio, overlap_ratio)

        if conflicting_drawings:
            conflict = OverlapConflict(
                text_block=text_block,
                conflicting_drawings=conflicting_drawings,
                max_overlap_ratio=max_ratio
            )
            conflicts.append(conflict)
            logger.debug(
                f"  Conflict: '{text_block.text}' at {text_block.bbox} "
                f"overlap={max_ratio:.1%}"
            )

    return conflicts