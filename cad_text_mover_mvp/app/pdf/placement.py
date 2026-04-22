from __future__ import annotations

import math
from typing import Iterable

import fitz

from app.pdf.geometry import PageGeometry
from app.pdf.types import Placement, RectBox, TextItem


class MarginPacker:
    def __init__(self, geometry: PageGeometry) -> None:
        self.geometry = geometry
        self.page_rect = geometry.page_rect
        self.page_diag = math.hypot(self.page_rect.width, self.page_rect.height)

    def place(
        self,
        item: TextItem,
        blockers: list[RectBox],
    ) -> Placement | None:
        if not self.geometry.margin_strips:
            return None

        scored_strips = sorted(
            self._score_strips(item, blockers),
            key=lambda entry: entry[1],
            reverse=True,
        )
        for strip_name, strip_score in scored_strips:
            strip = self.geometry.margin_strips[strip_name]
            layout = self._fit_text_layout(item.text, item.font_size, strip)
            if layout is None:
                continue
            render_text, render_font_size, box_w, box_h = layout
            candidate = self._search_strip(
                strip_name=strip_name,
                strip=strip,
                strip_score=strip_score,
                source=item,
                blockers=blockers,
                box_w=box_w,
                box_h=box_h,
                render_text=render_text,
                render_font_size=render_font_size,
            )
            if candidate is not None:
                return candidate
        return None

    def _score_strips(self, item: TextItem, blockers: list[RectBox]) -> list[tuple[str, float]]:
        source_edge = self._nearest_page_edge(item)
        scores: list[tuple[str, float]] = []
        for name, strip in self.geometry.margin_strips.items():
            free_area_ratio = self._estimate_free_area_ratio(strip, blockers)
            proximity = 1.0 - min(1.0, self._distance_to_strip(item.bbox, strip) / max(self.page_diag, 1.0))
            edge_bonus = 1.0 if name == source_edge else 0.0
            score = 0.50 * free_area_ratio + 0.35 * proximity + 0.15 * edge_bonus
            scores.append((name, round(score, 4)))
        return scores

    def _fit_text_layout(
        self,
        text: str,
        preferred_font_size: float,
        strip: RectBox,
    ) -> tuple[str, float, float, float] | None:
        outer_pad = 4.0
        inner_pad = 2.0
        max_width = strip.width - 2 * outer_pad
        max_height = strip.height - 2 * outer_pad
        if max_width <= 8 or max_height <= 8:
            return None

        font_size = min(max(preferred_font_size, 6.0), 14.0)
        while font_size >= 5.0:
            lines = self._wrap_text(text, font_size, max_width - 2 * inner_pad)
            if not lines:
                font_size -= 0.5
                continue
            longest = max(fitz.get_text_length(line, fontname="helv", fontsize=font_size) for line in lines)
            line_height = font_size * 1.25
            box_w = min(max_width, longest + 2 * inner_pad)
            box_h = len(lines) * line_height + 2 * inner_pad
            if box_w <= max_width and box_h <= max_height:
                render_text = "\n".join(lines)
                return render_text, round(font_size, 2), box_w, box_h
            font_size -= 0.5
        return None

    def _wrap_text(self, text: str, font_size: float, max_width: float) -> list[str]:
        words = text.split()
        if not words:
            return []
        if len(words) == 1:
            single = words[0]
            width = fitz.get_text_length(single, fontname="helv", fontsize=font_size)
            return [single] if width <= max_width else []

        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            tentative = f"{current} {word}"
            width = fitz.get_text_length(tentative, fontname="helv", fontsize=font_size)
            if width <= max_width:
                current = tentative
            else:
                if fitz.get_text_length(word, fontname="helv", fontsize=font_size) > max_width:
                    return []
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _search_strip(
        self,
        *,
        strip_name: str,
        strip: RectBox,
        strip_score: float,
        source: TextItem,
        blockers: list[RectBox],
        box_w: float,
        box_h: float,
        render_text: str,
        render_font_size: float,
    ) -> Placement | None:
        outer_pad = 4.0
        step = max(4.0, min(box_w, box_h) * 0.35)
        source_x, source_y = source.bbox.center

        if strip_name == "left":
            x = strip.x1 - box_w - outer_pad
            ys = self._range_candidates(strip.y0 + outer_pad, strip.y1 - box_h - outer_pad, step)
            ys = sorted(ys, key=lambda y: abs((y + box_h / 2.0) - source_y))
            rects = [RectBox(x, y, x + box_w, y + box_h) for y in ys]
        elif strip_name == "right":
            x = strip.x0 + outer_pad
            ys = self._range_candidates(strip.y0 + outer_pad, strip.y1 - box_h - outer_pad, step)
            ys = sorted(ys, key=lambda y: abs((y + box_h / 2.0) - source_y))
            rects = [RectBox(x, y, x + box_w, y + box_h) for y in ys]
        elif strip_name == "top":
            y = strip.y1 - box_h - outer_pad
            xs = self._range_candidates(strip.x0 + outer_pad, strip.x1 - box_w - outer_pad, step)
            xs = sorted(xs, key=lambda x: abs((x + box_w / 2.0) - source_x))
            rects = [RectBox(x, y, x + box_w, y + box_h) for x in xs]
        else:
            y = strip.y0 + outer_pad
            xs = self._range_candidates(strip.x0 + outer_pad, strip.x1 - box_w - outer_pad, step)
            xs = sorted(xs, key=lambda x: abs((x + box_w / 2.0) - source_x))
            rects = [RectBox(x, y, x + box_w, y + box_h) for x in xs]

        for rect in rects:
            if not strip.contains_rect(rect):
                continue
            if any(rect.intersects(blocker) for blocker in blockers):
                continue
            if self._rect_drawing_ratio(rect) > 0.02:
                continue
            distance = rect.distance_to_point(*source.bbox.center)
            placement_confidence = round(1.0 - min(1.0, distance / max(self.page_diag, 1.0)), 4)
            return Placement(
                strip_name=strip_name,
                target_bbox=rect,
                distance=round(distance, 3),
                strip_score=strip_score,
                placement_confidence=placement_confidence,
                render_text=render_text,
                render_font_size=render_font_size,
            )
        return None

    def _range_candidates(self, start: float, stop: float, step: float) -> list[float]:
        if stop < start:
            return []
        values: list[float] = []
        current = start
        while current <= stop + 0.001:
            values.append(round(current, 3))
            current += step
        return values or [round(start, 3)]

    def _estimate_free_area_ratio(self, strip: RectBox, blockers: list[RectBox]) -> float:
        blocked_area = 0.0
        strip_poly = strip.to_polygon()
        for blocker in blockers:
            blocked_area += strip_poly.intersection(blocker.to_polygon()).area
        ratio = 1.0 - min(1.0, blocked_area / max(strip.area, 1e-6))
        return max(0.0, min(1.0, ratio))

    def _distance_to_strip(self, bbox: RectBox, strip: RectBox) -> float:
        x = min(max(bbox.center[0], strip.x0), strip.x1)
        y = min(max(bbox.center[1], strip.y0), strip.y1)
        return math.hypot(bbox.center[0] - x, bbox.center[1] - y)

    def _nearest_page_edge(self, item: TextItem) -> str:
        cx, cy = item.bbox.center
        distances = {
            "left": abs(cx - self.page_rect.x0),
            "right": abs(self.page_rect.x1 - cx),
            "top": abs(cy - self.page_rect.y0),
            "bottom": abs(self.page_rect.y1 - cy),
        }
        return min(distances, key=distances.get)

    def _rect_drawing_ratio(self, rect: RectBox) -> float:
        x0 = max(0, int(rect.x0 * self.geometry.scale_x))
        y0 = max(0, int(rect.y0 * self.geometry.scale_y))
        x1 = min(self.geometry.drawing_mask.shape[1], int(math.ceil(rect.x1 * self.geometry.scale_x)))
        y1 = min(self.geometry.drawing_mask.shape[0], int(math.ceil(rect.y1 * self.geometry.scale_y)))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        crop = self.geometry.drawing_mask[y0:y1, x0:x1]
        if crop.size == 0:
            return 0.0
        return float((crop > 0).sum()) / float(crop.size)
