from __future__ import annotations

import math
from statistics import quantiles
from typing import Any

import numpy as np
from shapely.geometry import Point

from app.pdf.geometry import PageGeometry
from app.pdf.types import Placement, RectBox, TextItem


class TextDecisionEngine:
    def __init__(
        self,
        *,
        overlap_move_threshold: float,
        overlap_review_threshold: float,
        max_rotation_degrees: float,
        max_relocate_chars: int,
    ) -> None:
        self.overlap_move_threshold = overlap_move_threshold
        self.overlap_review_threshold = overlap_review_threshold
        self.max_rotation_degrees = max_rotation_degrees
        self.max_relocate_chars = max_relocate_chars

    def annotate_items(self, items: list[TextItem], geometry: PageGeometry) -> list[TextItem]:
        font_sizes = [item.font_size for item in items if item.font_size > 0]
        if len(font_sizes) >= 2:
            large_font_threshold = max(18.0, quantiles(font_sizes, n=10)[-1] * 1.25)
        else:
            large_font_threshold = 18.0

        for item in items:
            metrics = self._compute_overlap_metrics(item, geometry)
            item.overlap_metrics = metrics
            item.overlap_score = metrics["overlap_score"]
            self._classify_item(item, geometry, large_font_threshold)
        return items

    def final_confidence(self, item: TextItem, placement: Placement | None) -> float:
        extraction = max(0.0, min(1.0, item.extraction_confidence))
        overlap = max(0.0, min(1.0, item.overlap_score / 0.80))
        placement_conf = placement.placement_confidence if placement else 0.0
        confidence = 0.40 * extraction + 0.35 * overlap + 0.25 * placement_conf
        if "ocr_used" in item.review_flags:
            confidence *= 0.92
        if "rotated_text" in item.review_flags:
            confidence *= 0.85
        return round(max(0.0, min(1.0, confidence)), 4)

    def _compute_overlap_metrics(self, item: TextItem, geometry: PageGeometry) -> dict[str, float]:
        bbox_poly = item.bbox.to_polygon()
        bbox_area = max(item.bbox.area, 1e-6)
        expanded_box = item.bbox.expanded(max(2.0, item.font_size * 0.15))
        expanded_poly = expanded_box.to_polygon()
        expanded_area = max(expanded_box.area, 1e-6)

        geom_intersection = 0.0
        expanded_geom_intersection = 0.0
        center_in_drawing = 0.0
        if not geometry.drawing_union.is_empty:
            geom_intersection = bbox_poly.intersection(geometry.drawing_union).area
            expanded_geom_intersection = expanded_poly.intersection(geometry.drawing_union).area
            center_point = Point(item.bbox.center)
            center_in_drawing = 1.0 if geometry.drawing_union.buffer(0.25).contains(center_point) else 0.0

        ioa_geometry = geom_intersection / bbox_area
        density_geometry = expanded_geom_intersection / expanded_area
        ioa_mask = self._mask_ratio(item.bbox, geometry)
        density_mask = self._mask_ratio(expanded_box, geometry)
        if center_in_drawing == 0.0:
            center_px = self._mask_contains_center(item.bbox, geometry)
            center_in_drawing = 1.0 if center_px else 0.0

        direct_overlap = max(ioa_geometry, ioa_mask)
        expanded_density = max(density_geometry, density_mask)
        overlap_score = 0.60 * direct_overlap + 0.25 * expanded_density + 0.15 * center_in_drawing

        return {
            "direct_overlap": round(direct_overlap, 4),
            "expanded_density": round(expanded_density, 4),
            "center_in_drawing": round(center_in_drawing, 4),
            "ioa_geometry": round(ioa_geometry, 4),
            "ioa_mask": round(ioa_mask, 4),
            "overlap_score": round(max(0.0, min(1.0, overlap_score)), 4),
        }

    def _classify_item(self, item: TextItem, geometry: PageGeometry, large_font_threshold: float) -> None:
        text_len = len(item.text.strip())
        cx, cy = item.bbox.center
        page = geometry.page_rect
        top_band = page.y0 + page.height * 0.08
        bottom_band = page.y1 - page.height * 0.08
        left_band = page.x0 + page.width * 0.06
        right_band = page.x1 - page.width * 0.06
        in_title_block_corner = cx > page.x0 + page.width * 0.78 and cy > page.y0 + page.height * 0.78
        near_edge = cx < left_band or cx > right_band or cy < top_band or cy > bottom_band

        if item.extraction_method == "ocr":
            item.review_flags.append("ocr_used")
        if item.extraction_confidence < 0.60:
            item.review_flags.append("low_extraction_confidence")
        if abs(item.angle_degrees) > self.max_rotation_degrees:
            item.review_flags.append("rotated_text")
        if text_len > self.max_relocate_chars:
            item.review_flags.append("text_too_long")
        if item.font_size >= large_font_threshold and cy < page.y0 + page.height * 0.20:
            item.review_flags.append("large_title_text")

        if (
            "text_too_long" in item.review_flags
            or ("rotated_text" in item.review_flags and item.overlap_score >= self.overlap_review_threshold)
            or ("large_title_text" in item.review_flags and item.overlap_score < self.overlap_move_threshold)
        ):
            item.classification = "review"
            return

        if in_title_block_corner and item.overlap_score < self.overlap_move_threshold:
            item.classification = "keep"
            item.review_flags.append("title_block_text")
            return

        if near_edge and item.overlap_score < self.overlap_review_threshold:
            item.classification = "keep"
            item.review_flags.append("margin_or_header_text")
            return

        if item.overlap_score >= self.overlap_move_threshold:
            item.classification = "move"
            return

        if item.overlap_score >= self.overlap_review_threshold:
            item.classification = "review"
            item.review_flags.append("weak_overlap")
            return

        item.classification = "keep"

    def _mask_ratio(self, rect: RectBox, geometry: PageGeometry) -> float:
        x0 = max(0, int(rect.x0 * geometry.scale_x))
        y0 = max(0, int(rect.y0 * geometry.scale_y))
        x1 = min(geometry.drawing_mask.shape[1], int(math.ceil(rect.x1 * geometry.scale_x)))
        y1 = min(geometry.drawing_mask.shape[0], int(math.ceil(rect.y1 * geometry.scale_y)))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        crop = geometry.drawing_mask[y0:y1, x0:x1]
        if crop.size == 0:
            return 0.0
        return float(np.count_nonzero(crop)) / float(crop.size)

    def _mask_contains_center(self, rect: RectBox, geometry: PageGeometry) -> bool:
        cx, cy = rect.center
        px = min(max(int(cx * geometry.scale_x), 0), geometry.drawing_mask.shape[1] - 1)
        py = min(max(int(cy * geometry.scale_y), 0), geometry.drawing_mask.shape[0] - 1)
        return bool(geometry.drawing_mask[py, px] > 0)
