from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import fitz
import numpy as np
from shapely.geometry import GeometryCollection, Polygon, box
from shapely.ops import unary_union

from app.pdf.types import RectBox, TextItem


@dataclass
class PageGeometry:
    page_rect: RectBox
    image_rgb: np.ndarray
    content_mask: np.ndarray
    drawing_mask: np.ndarray
    scale_x: float
    scale_y: float
    contour_geometries: list[Any]
    vector_geometries: list[Any]
    drawing_union: Any
    margin_strips: dict[str, RectBox]

    @property
    def drawing_component_count(self) -> int:
        return len(self.contour_geometries) + len(self.vector_geometries)


class PageGeometryExtractor:
    def __init__(self, *, margin_density_threshold: float = 0.015, min_margin_points: float = 18.0) -> None:
        self.margin_density_threshold = margin_density_threshold
        self.min_margin_points = min_margin_points

    def render_page(self, page: fitz.Page, dpi: int) -> tuple[np.ndarray, float, float]:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
        elif pix.n != 3:
            image = np.repeat(image, 3, axis=2)
        page_rect = RectBox.from_fitz(page.rect)
        scale_x = pix.width / max(page_rect.width, 1.0)
        scale_y = pix.height / max(page_rect.height, 1.0)
        return image, scale_x, scale_y

    def extract(
        self,
        *,
        page: fitz.Page,
        image_rgb: np.ndarray,
        text_items: list[TextItem],
        scale_x: float,
        scale_y: float,
    ) -> PageGeometry:
        page_rect = RectBox.from_fitz(page.rect)
        text_mask = self._build_text_mask(text_items, image_rgb.shape[:2], scale_x, scale_y)
        content_mask = self._build_content_mask(image_rgb)
        drawing_mask = self._remove_text_and_small_noise(content_mask, text_mask)
        contour_geometries = self._mask_to_geometries(drawing_mask, scale_x, scale_y)
        vector_geometries = self._extract_vector_geometries(page)
        all_geometries = contour_geometries + vector_geometries
        if all_geometries:
            drawing_union = unary_union(all_geometries)
        else:
            drawing_union = GeometryCollection()
        margin_strips = self._compute_margin_strips(
            drawing_mask=drawing_mask,
            page_rect=page_rect,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        return PageGeometry(
            page_rect=page_rect,
            image_rgb=image_rgb,
            content_mask=content_mask,
            drawing_mask=drawing_mask,
            scale_x=scale_x,
            scale_y=scale_y,
            contour_geometries=contour_geometries,
            vector_geometries=vector_geometries,
            drawing_union=drawing_union,
            margin_strips=margin_strips,
        )

    def _build_text_mask(
        self,
        text_items: list[TextItem],
        shape: tuple[int, int],
        scale_x: float,
        scale_y: float,
    ) -> np.ndarray:
        height, width = shape
        mask = np.zeros((height, width), dtype=np.uint8)
        for item in text_items:
            pad_x = max(1, int(item.font_size * 0.12 * scale_x))
            pad_y = max(1, int(item.font_size * 0.18 * scale_y))
            x0 = max(0, int(item.bbox.x0 * scale_x) - pad_x)
            y0 = max(0, int(item.bbox.y0 * scale_y) - pad_y)
            x1 = min(width, int(item.bbox.x1 * scale_x) + pad_x)
            y1 = min(height, int(item.bbox.y1 * scale_y) + pad_y)
            cv2.rectangle(mask, (x0, y0), (x1, y1), 255, thickness=-1)
        return mask

    def _build_content_mask(self, image_rgb: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        _, fixed = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)
        adaptive = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_MEAN_C,
            cv2.THRESH_BINARY_INV,
            31,
            15,
        )
        combined = cv2.bitwise_or(fixed, adaptive)
        combined = cv2.medianBlur(combined, 3)
        return combined

    def _remove_text_and_small_noise(self, content_mask: np.ndarray, text_mask: np.ndarray) -> np.ndarray:
        without_text = cv2.bitwise_and(content_mask, cv2.bitwise_not(text_mask))
        kernel = np.ones((3, 3), dtype=np.uint8)
        closed = cv2.morphologyEx(without_text, cv2.MORPH_CLOSE, kernel, iterations=1)
        opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=1)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
        filtered = np.zeros_like(opened)
        min_area = max(12, int(opened.shape[0] * opened.shape[1] * 0.00001))
        for label in range(1, num_labels):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area >= min_area:
                filtered[labels == label] = 255
        return filtered

    def _mask_to_geometries(self, drawing_mask: np.ndarray, scale_x: float, scale_y: float) -> list[Any]:
        contours, _ = cv2.findContours(drawing_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        geometries: list[Any] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 12:
                continue
            epsilon = max(1.0, 0.0025 * cv2.arcLength(contour, True))
            approx = cv2.approxPolyDP(contour, epsilon, True)
            points = [(float(pt[0][0]) / scale_x, float(pt[0][1]) / scale_y) for pt in approx]
            if len(points) < 3:
                continue
            polygon = Polygon(points)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            if polygon.is_empty or polygon.area <= 0:
                continue
            geometries.append(polygon)
        return geometries

    def _extract_vector_geometries(self, page: fitz.Page) -> list[Any]:
        geometries: list[Any] = []
        drawings = page.get_drawings()
        if drawings:
            try:
                clusters = page.cluster_drawings(drawings=drawings, x_tolerance=3, y_tolerance=3, final_filter=True)
            except Exception:
                clusters = []
            for raw_rect in clusters:
                rect = RectBox.from_tuple(raw_rect)
                if rect.area <= 4:
                    continue
                geometries.append(box(rect.x0, rect.y0, rect.x1, rect.y1))
            if not geometries:
                for path in drawings:
                    raw_rect = path.get("rect")
                    if not raw_rect:
                        continue
                    rect = RectBox.from_fitz(raw_rect if isinstance(raw_rect, fitz.Rect) else fitz.Rect(raw_rect))
                    if rect.area <= 4:
                        continue
                    geometries.append(box(rect.x0, rect.y0, rect.x1, rect.y1))
        return geometries

    def _compute_margin_strips(
        self,
        *,
        drawing_mask: np.ndarray,
        page_rect: RectBox,
        scale_x: float,
        scale_y: float,
    ) -> dict[str, RectBox]:
        column_density = np.count_nonzero(drawing_mask, axis=0) / max(drawing_mask.shape[0], 1)
        row_density = np.count_nonzero(drawing_mask, axis=1) / max(drawing_mask.shape[1], 1)
        column_density = self._smooth_density(column_density)
        row_density = self._smooth_density(row_density)

        left_cols = self._scan_margin_prefix(column_density)
        right_cols = self._scan_margin_prefix(column_density[::-1])
        top_rows = self._scan_margin_prefix(row_density)
        bottom_rows = self._scan_margin_prefix(row_density[::-1])

        left_width = left_cols / scale_x
        right_width = right_cols / scale_x
        top_height = top_rows / scale_y
        bottom_height = bottom_rows / scale_y

        strips: dict[str, RectBox] = {}
        if left_width >= self.min_margin_points:
            strips["left"] = RectBox(page_rect.x0, page_rect.y0, page_rect.x0 + left_width, page_rect.y1)
        if right_width >= self.min_margin_points:
            strips["right"] = RectBox(page_rect.x1 - right_width, page_rect.y0, page_rect.x1, page_rect.y1)
        if top_height >= self.min_margin_points:
            strips["top"] = RectBox(page_rect.x0, page_rect.y0, page_rect.x1, page_rect.y0 + top_height)
        if bottom_height >= self.min_margin_points:
            strips["bottom"] = RectBox(page_rect.x0, page_rect.y1 - bottom_height, page_rect.x1, page_rect.y1)
        return strips

    def _smooth_density(self, density: np.ndarray, window: int = 9) -> np.ndarray:
        if density.size == 0:
            return density
        if density.size < window:
            return density
        kernel = np.ones(window, dtype=np.float32) / float(window)
        return np.convolve(density, kernel, mode="same")

    def _scan_margin_prefix(self, density: np.ndarray, consecutive: int = 4) -> int:
        if density.size == 0:
            return 0
        high_run = 0
        for idx, value in enumerate(density):
            if value <= self.margin_density_threshold:
                high_run = 0
                continue
            high_run += 1
            if high_run >= consecutive:
                return max(0, idx - consecutive + 1)
        return int(density.size)
