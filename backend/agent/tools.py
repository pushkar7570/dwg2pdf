# backend/agent/tools.py

import json
import math
from typing import List, Tuple, Dict, Any
from shapely.geometry import box as shapely_box
from langchain.tools import tool
from pipeline.utils import get_logger

logger = get_logger(__name__)


# ── Shared session state ──────────────────────────────────────────────────────
_session_state: Dict[str, Any] = {
    "page_dimensions"    : [],   # [(w,h), ...] per page
    "all_text_bboxes"    : [],   # all text bboxes in document
    "all_drawing_bboxes" : [],   # all drawing element bboxes
    "filled_zones"       : {},   # {page_no: [bbox, ...]} filled regions
    "free_zones"         : {},   # {page_no: [bbox, ...]} safe for text
    "committed_positions": [],   # fixes committed this session
}


def initialize_session(
    page_dimensions  : List[Tuple],
    text_bboxes      : List[Tuple],
    drawing_bboxes   : List[Tuple],
    filled_zones     : Dict[int, List] = None,
    free_zones       : Dict[int, List] = None,
):
    """Initialize agent session state before processing a document."""
    _session_state["page_dimensions"]     = list(page_dimensions)
    _session_state["all_text_bboxes"]     = list(text_bboxes)
    _session_state["all_drawing_bboxes"]  = list(drawing_bboxes)
    _session_state["filled_zones"]        = filled_zones  or {}
    _session_state["free_zones"]          = free_zones    or {}
    _session_state["committed_positions"] = []


def get_committed_positions() -> List[dict]:
    """Return all fixes committed during this session."""
    return _session_state["committed_positions"]


# ── Agent Tools ───────────────────────────────────────────────────────────────

@tool
def get_page_context(page_no: int) -> str:
    """
    Get page dimensions, zone summary, and element counts.
    Always call this first before working on any page.

    Args:
        page_no: Page number starting from 0
    """
    try:
        dims = _session_state["page_dimensions"]
        if page_no >= len(dims):
            return json.dumps({"error": f"Page {page_no} not found"})

        pw, ph      = dims[page_no]
        filled      = _session_state["filled_zones"].get(page_no, [])
        free        = _session_state["free_zones"].get(page_no, [])
        committed   = [
            p for p in _session_state["committed_positions"]
            if p.get("page_no") == page_no
        ]

        return json.dumps({
            "page_no"             : page_no,
            "width"               : pw,
            "height"              : ph,
            "filled_zones_count"  : len(filled),
            "free_zones_count"    : len(free),
            "free_zones"          : [list(fz) for fz in free[:5]],
            "total_drawings"      : len(_session_state["all_drawing_bboxes"]),
            "fixes_so_far"        : len(committed),
            "note"                : (
                "Place text in free_zones only. "
                "Never place inside filled_zones."
            ),
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def analyze_free_spaces(
    page_no      : int,
    center_x     : float,
    center_y     : float,
    search_radius: float = 150.0,
) -> str:
    """
    Find positions near (center_x, center_y) where text can be
    placed safely — outside filled drawing regions.
    Prefers positions inside known free/margin zones.

    Args:
        page_no      : Page number (0-indexed)
        center_x     : X coordinate to search around
        center_y     : Y coordinate to search around
        search_radius: Search radius in pixels (default 150)
    """
    try:
        dims = _session_state["page_dimensions"]
        if page_no >= len(dims):
            return json.dumps({"error": f"Page {page_no} not found"})

        pw, ph = dims[page_no]

        # Zones where we must NOT place text
        forbidden_bboxes = _session_state["filled_zones"].get(page_no, [])
        forbidden_shapes = [shapely_box(*b) for b in forbidden_bboxes]

        # All occupied space (drawings + already committed text)
        occupied = (
            [shapely_box(*b) for b in _session_state["all_drawing_bboxes"]]
            + [
                shapely_box(*p["new_bbox"])
                for p in _session_state["committed_positions"]
                if p.get("page_no") == page_no
            ]
        )

        # Known safe zones (page margins)
        safe_bboxes  = _session_state["free_zones"].get(page_no, [])
        safe_shapes  = [shapely_box(*sz) for sz in safe_bboxes]

        free   = []
        step   = 15
        radius = int(search_radius)

        for dx in range(-radius, radius + step, step):
            for dy in range(-radius, radius + step, step):
                x = center_x + dx
                y = center_y + dy

                # Page boundary check
                if x < 5 or y < 5 or x > pw - 60 or y > ph - 15:
                    continue

                probe = shapely_box(x, y, x + 40, y + 12)

                # Reject if inside forbidden zone
                if any(probe.intersects(fz) for fz in forbidden_shapes):
                    continue

                # Reject if overlaps drawings or committed text
                if any(probe.intersects(occ) for occ in occupied):
                    continue

                dist       = math.sqrt(dx ** 2 + dy ** 2)
                in_safe_zone = bool(safe_shapes and any(
                    probe.within(sz) for sz in safe_shapes
                ))

                free.append({
                    "x"           : round(x, 1),
                    "y"           : round(y, 1),
                    "dist"        : round(dist, 1),
                    "in_safe_zone": in_safe_zone,
                })

        # Sort: safe zone positions first, then by distance
        free.sort(key=lambda p: (0 if p["in_safe_zone"] else 1, p["dist"]))

        # If nothing found in radius, expand search automatically
        if not free and search_radius < 400:
            logger.debug(
                f"No free space in radius {search_radius}, expanding..."
            )
            return analyze_free_spaces.invoke({
                "page_no"      : page_no,
                "center_x"     : center_x,
                "center_y"     : center_y,
                "search_radius": search_radius * 2,
            })

        return json.dumps({
            "page_no"           : page_no,
            "spaces_found"      : len(free),
            "top_candidates"    : free[:15],
            "safe_zone_count"   : sum(1 for p in free if p["in_safe_zone"]),
            "note"              : (
                "Prefer candidates where in_safe_zone=true. "
                "These are in page margin areas outside the drawing."
            ),
        }, indent=2)

    except Exception as e:
        logger.error(f"analyze_free_spaces error: {e}")
        return json.dumps({"error": str(e)})


@tool
def validate_position(
    page_no    : int,
    x0         : float,
    y0         : float,
    text_width : float,
    text_height: float,
) -> str:
    """
    Validate that placing text at (x0, y0) with the given size
    will not overlap drawing elements, filled zones, or
    previously committed text positions.

    Args:
        page_no    : Page number (0-indexed)
        x0         : Left edge of proposed position
        y0         : Top edge of proposed position
        text_width : Width of the text block in pixels
        text_height: Height of the text block in pixels
    """
    try:
        dims = _session_state["page_dimensions"]
        if page_no >= len(dims):
            return json.dumps({"error": f"Page {page_no} not found"})

        pw, ph = dims[page_no]

        # Boundary check
        if x0 < 0 or y0 < 0 or (x0 + text_width) > pw or (y0 + text_height) > ph:
            return json.dumps({
                "is_valid"  : False,
                "reason"    : "Outside page boundaries",
                "page_size" : [pw, ph],
            })

        # Test shape with 5px clearance buffer
        probe = shapely_box(
            x0 - 5, y0 - 5,
            x0 + text_width + 5,
            y0 + text_height + 5,
        )

        # Check against filled zones
        filled     = _session_state["filled_zones"].get(page_no, [])
        in_filled  = [
            list(b) for b in filled
            if probe.intersects(shapely_box(*b))
        ]

        # Check against all drawing elements
        draw_conflicts = [
            list(b) for b in _session_state["all_drawing_bboxes"]
            if probe.intersects(shapely_box(*b))
        ]

        # Check against committed positions
        text_conflicts = [
            p["text"]
            for p in _session_state["committed_positions"]
            if p.get("page_no") == page_no
            and probe.intersects(shapely_box(*p["new_bbox"]))
        ]

        is_valid = (
            len(in_filled)      == 0 and
            len(draw_conflicts) == 0 and
            len(text_conflicts) == 0
        )

        return json.dumps({
            "is_valid"         : is_valid,
            "proposed_bbox"    : [x0, y0, x0 + text_width, y0 + text_height],
            "in_filled_zone"   : in_filled[:3],
            "drawing_conflicts": draw_conflicts[:3],
            "text_conflicts"   : text_conflicts[:3],
            "recommendation"   : "ACCEPT" if is_valid else "REJECT - try another position",
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def commit_repositioning(
    page_no      : int,
    text         : str,
    original_bbox: list,
    new_x0       : float,
    new_y0       : float,
    font_size    : float = 8.0,
    color        : list  = None,
) -> str:
    """
    Commit a repositioning decision. Call this after
    validate_position confirms the position is valid.
    The new position is registered so future validations
    account for it.

    Args:
        page_no      : Page number (0-indexed)
        text         : Text content being moved
        original_bbox: Original [x0, y0, x1, y1]
        new_x0       : New left edge X coordinate
        new_y0       : New top edge Y coordinate
        font_size    : Original font size (default 8.0)
        color        : RGB color [r,g,b] in 0-1 range (default black)
    """
    try:
        if color is None:
            color = [0.0, 0.0, 0.0]

        orig     = list(original_bbox)
        tw       = orig[2] - orig[0]
        th       = orig[3] - orig[1]
        new_bbox = [new_x0, new_y0, new_x0 + tw, new_y0 + th]

        _session_state["committed_positions"].append({
            "page_no"      : page_no,
            "text"         : text,
            "original_bbox": orig,
            "new_bbox"     : new_bbox,
            "font_size"    : font_size,
            "color"        : color,
        })

        logger.info(
            f"Committed: '{text}' "
            f"{[round(v,1) for v in orig]} → "
            f"{[round(v,1) for v in new_bbox]}"
        )

        return json.dumps({
            "status"    : "committed",
            "text"      : text,
            "moved_from": orig,
            "moved_to"  : new_bbox,
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool list exported to agent ───────────────────────────────────────────────
ALL_TOOLS = [
    get_page_context,
    analyze_free_spaces,
    validate_position,
    commit_repositioning,
]
