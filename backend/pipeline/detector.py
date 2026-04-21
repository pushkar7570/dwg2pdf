# backend/pipeline/detector.py

from shapely.geometry import box as shapely_box, Point
from shapely.strtree import STRtree
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from .parser import ParsedDocument, TextBlock, DrawingElement, ParsedPage
from .utils import get_logger

logger = get_logger(__name__)

OVERLAP_THRESHOLD = 0.05
FILLED_ZONE_RATIO = 0.005


@dataclass
class PageZones:
    page_no     : int
    page_width  : float
    page_height : float
    drawing_zone : Optional[Tuple] = None
    title_block  : Optional[Tuple] = None
    filled_zones : List = field(default_factory=list)
    free_zones   : List = field(default_factory=list)
    margin_zones : List = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "page_no"     : self.page_no,
            "page_width"  : self.page_width,
            "page_height" : self.page_height,
            "drawing_zone": list(self.drawing_zone) if self.drawing_zone else None,
            "title_block" : list(self.title_block)  if self.title_block  else None,
            "filled_zones": [list(z) for z in self.filled_zones],
            "free_zones"  : [list(z) for z in self.free_zones],
            "margin_zones": [list(z) for z in self.margin_zones],
        }


@dataclass
class OverlapConflict:
    text_block           : TextBlock
    conflicting_drawings : List[DrawingElement] = field(default_factory=list)
    max_overlap_ratio    : float = 0.0
    conflict_type        : str   = "bbox_overlap"

    @property
    def text(self)         -> str  : return self.text_block.text
    @property
    def current_bbox(self) -> tuple: return self.text_block.bbox
    @property
    def page_no(self)      -> int  : return self.text_block.page_no

    def to_dict(self) -> dict:
        return {
            "text"                      : self.text,
            "current_bbox"              : list(self.current_bbox),
            "page_no"                   : self.page_no,
            "font_size"                 : self.text_block.font_size,
            "font_name"                 : self.text_block.font_name,
            "color"                     : list(self.text_block.color),
            "rotation"                  : self.text_block.rotation,
            "max_overlap_ratio"         : round(self.max_overlap_ratio, 3),
            "conflict_type"             : self.conflict_type,
            "conflicting_drawing_bboxes": [list(d.bbox) for d in self.conflicting_drawings],
        }


@dataclass
class DetectionResult:
    has_overlaps   : bool
    total_conflicts: int
    conflicts      : List[OverlapConflict] = field(default_factory=list)
    page_zones     : List[PageZones]       = field(default_factory=list)
    summary        : str = ""

    def to_dict(self) -> dict:
        return {
            "has_overlaps"   : self.has_overlaps,
            "total_conflicts": self.total_conflicts,
            "summary"        : self.summary,
            "conflicts"      : [c.to_dict() for c in self.conflicts],
            "page_zones"     : [z.to_dict() for z in self.page_zones],
        }


def detect_overlaps(parsed_doc: ParsedDocument) -> DetectionResult:
    logger.info("Running zone-aware overlap detection...")
    all_conflicts : List[OverlapConflict] = []
    all_zones     : List[PageZones]       = []

    for page in parsed_doc.pages:
        zones     = _analyze_page_zones(page)
        conflicts = _detect_page_overlaps(page, zones)
        all_zones.append(zones)
        all_conflicts.extend(conflicts)
        logger.info(
            f"  Page {page.page_no}: "
            f"{len(conflicts)} conflict(s), "
            f"{len(zones.free_zones)} free zone(s)"
        )

    has_overlaps = len(all_conflicts) > 0
    summary = (
        f"Found {len(all_conflicts)} overlapping text block(s) "
        f"across {parsed_doc.page_count} page(s)."
        if has_overlaps
        else "No overlaps detected. Drawing is clean."
    )
    logger.info(summary)

    return DetectionResult(
        has_overlaps=has_overlaps,
        total_conflicts=len(all_conflicts),
        conflicts=all_conflicts,
        page_zones=all_zones,
        summary=summary,
    )


def _analyze_page_zones(page: ParsedPage) -> PageZones:
    pw, ph = page.width, page.height
    zones  = PageZones(page_no=page.page_no, page_width=pw, page_height=ph)

    if not page.drawing_elements:
        zones.free_zones   = [(5.0, 5.0, pw-5.0, ph-5.0)]
        zones.margin_zones = [(5.0, 5.0, pw-5.0, ph-5.0)]
        return zones

    page_area  = pw * ph
    all_bboxes = [d.bbox for d in page.drawing_elements]

    for elem in page.drawing_elements:
        if not elem.is_filled:
            continue
        bx0,by0,bx1,by1 = elem.bbox
        elem_area = max(0, bx1-bx0) * max(0, by1-by0)
        if elem_area / max(page_area,1) > FILLED_ZONE_RATIO:
            zones.filled_zones.append(elem.bbox)

    min_x = min(b[0] for b in all_bboxes)
    min_y = min(b[1] for b in all_bboxes)
    max_x = max(b[2] for b in all_bboxes)
    max_y = max(b[3] for b in all_bboxes)
    zones.drawing_zone = (min_x, min_y, max_x, max_y)

    title_y = ph * 0.85
    if any(b[1] > title_y for b in all_bboxes):
        zones.title_block = (0.0, title_y, pw, ph)

    dz     = zones.drawing_zone
    margin = 15.0
    bottom_bound = zones.title_block[1] if zones.title_block else ph - margin

    if dz[1] > margin * 2:
        zones.margin_zones.append((margin, margin, pw-margin, dz[1]-margin))
    if bottom_bound - dz[3] > margin * 2:
        zones.margin_zones.append((margin, dz[3]+margin, pw-margin, bottom_bound))
    if dz[0] > margin * 2:
        zones.margin_zones.append((margin, dz[1], dz[0]-margin, dz[3]))
    if pw - dz[2] > margin * 2:
        zones.margin_zones.append((dz[2]+margin, dz[1], pw-margin, dz[3]))

    if not zones.margin_zones:
        zones.margin_zones = [(5.0, 5.0, pw-5.0, ph-5.0)]

    draw_shapes   = [shapely_box(*b) for b in all_bboxes]
    spatial_index = STRtree(draw_shapes) if draw_shapes else None

    for mz in zones.margin_zones:
        mz_shape    = shapely_box(*mz)
        has_content = False
        if spatial_index:
            candidates  = spatial_index.query(mz_shape)
            has_content = any(mz_shape.intersects(draw_shapes[i]) for i in candidates)
        if not has_content:
            zones.free_zones.append(mz)

    if not zones.free_zones:
        zones.free_zones = list(zones.margin_zones)

    return zones


def _detect_page_overlaps(page: ParsedPage, zones: PageZones) -> List[OverlapConflict]:
    conflicts = []
    if not page.text_blocks:
        return conflicts

    drawing_shapes = [shapely_box(*d.bbox) for d in page.drawing_elements]
    filled_shapes  = [shapely_box(*fz)     for fz in zones.filled_zones]

    spatial_index = STRtree(drawing_shapes) if drawing_shapes else None
    filled_index  = STRtree(filled_shapes)  if filled_shapes  else None

    for tb in page.text_blocks:
        text_shape  = shapely_box(*tb.bbox)
        text_center = Point(*tb.center)
        text_area   = text_shape.area
        if text_area < 1.0:
            continue

        conflict_type        = None
        conflicting_drawings = []
        max_ratio            = 0.0

        if filled_index and zones.filled_zones:
            for idx in filled_index.query(text_shape):
                if idx < len(filled_shapes) and filled_shapes[idx].contains(text_center):
                    conflict_type = "inside_filled"
                    max_ratio     = 1.0
                    fz_bbox = zones.filled_zones[idx]
                    for d in page.drawing_elements:
                        if abs(d.bbox[0]-fz_bbox[0]) < 0.5 and abs(d.bbox[1]-fz_bbox[1]) < 0.5:
                            conflicting_drawings.append(d)
                            break
                    break

        if not conflict_type and spatial_index:
            for idx in spatial_index.query(text_shape):
                if idx >= len(drawing_shapes):
                    continue
                draw       = page.drawing_elements[idx]
                draw_shape = drawing_shapes[idx]
                if not text_shape.intersects(draw_shape):
                    continue
                try:
                    overlap = text_shape.intersection(draw_shape).area / text_area
                except Exception:
                    overlap = 0.0
                if overlap > OVERLAP_THRESHOLD:
                    conflicting_drawings.append(draw)
                    max_ratio = max(max_ratio, overlap)
            if conflicting_drawings:
                conflict_type = "bbox_overlap"

        if conflict_type:
            conflicts.append(OverlapConflict(
                text_block=tb,
                conflicting_drawings=conflicting_drawings,
                max_overlap_ratio=max_ratio,
                conflict_type=conflict_type,
            ))
    return conflicts
