import json
import math
from typing import List, Tuple, Dict, Any
from shapely.geometry import box as shapely_box
from langchain.tools import tool
from pipeline.utils import get_logger

logger = get_logger(__name__)

# ── Shared session state ──────────────────────────────────────────────────────
_session_state: Dict[str, Any] = {
    "page_dimensions"   : [],
    "all_text_bboxes"   : [],
    "all_drawing_bboxes": [],
    "committed_positions": [],
}


def initialize_session(
    page_dimensions : List[Tuple],
    text_bboxes     : List[Tuple],
    drawing_bboxes  : List[Tuple],
):
    _session_state["page_dimensions"]    = list(page_dimensions)
    _session_state["all_text_bboxes"]    = list(text_bboxes)
    _session_state["all_drawing_bboxes"] = list(drawing_bboxes)
    _session_state["committed_positions"] = []


def get_committed_positions() -> List[dict]:
    return _session_state["committed_positions"]


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def get_page_context(page_no: int) -> str:
    """
    Get page dimensions and element counts.
    Call this first before analyzing any page.

    Args:
        page_no: Page number starting from 0
    """
    try:
        dims = _session_state["page_dimensions"]
        if page_no >= len(dims):
            return json.dumps({"error": f"Page {page_no} not found"})

        pw, ph = dims[page_no]
        committed = [
            p for p in _session_state["committed_positions"]
            if p.get("page_no") == page_no
        ]

        return json.dumps({
            "page_no"           : page_no,
            "width"             : pw,
            "height"            : ph,
            "total_drawings"    : len(_session_state["all_drawing_bboxes"]),
            "fixes_committed"   : len(committed),
            "coordinate_system" : "top-left origin, x right, y down",
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def analyze_free_spaces(
    page_no  : int,
    center_x : float,
    center_y : float,
    search_radius: float = 120.0,
) -> str:
    """
    Find free spaces near a point where text can be placed
    without overlapping drawing elements.

    Args:
        page_no      : Page number (0-indexed)
        center_x     : X coordinate to search around
        center_y     : Y coordinate to search around
        search_radius: Search radius in pixels (default 120)
    """
    try:
        dims = _session_state["page_dimensions"]
        if page_no >= len(dims):
            return json.dumps({"error": f"Page {page_no} not found"})

        pw, ph = dims[page_no]

        occupied_bboxes = list(_session_state["all_drawing_bboxes"]) + [
            p["new_bbox"]
            for p in _session_state["committed_positions"]
            if p.get("page_no") == page_no
        ]
        occupied_shapes = [shapely_box(*b) for b in occupied_bboxes]

        free = []
        step = 20

        for dx in range(-int(search_radius), int(search_radius) + step, step):
            for dy in range(-int(search_radius), int(search_radius) + step, step):
                x = center_x + dx
                y = center_y + dy

                if x < 5 or y < 5 or x > pw - 60 or y > ph - 15:
                    continue

                probe = shapely_box(x, y, x + 40, y + 12)
                if not any(probe.intersects(occ) for occ in occupied_shapes):
                    dist = math.sqrt(dx ** 2 + dy ** 2)
                    free.append({
                        "x"   : round(x, 1),
                        "y"   : round(y, 1),
                        "dist": round(dist, 1),
                    })

        free.sort(key=lambda p: p["dist"])

        return json.dumps({
            "page_no"        : page_no,
            "center"         : {"x": center_x, "y": center_y},
            "spaces_found"   : len(free),
            "top_candidates" : free[:15],
        }, indent=2)

    except Exception as e:
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
    Check whether placing text at (x0, y0) with given size
    will overlap any drawing element or committed text.

    Args:
        page_no     : Page number (0-indexed)
        x0          : Left edge of proposed position
        y0          : Top edge of proposed position
        text_width  : Width of the text block
        text_height : Height of the text block
    """
    try:
        dims = _session_state["page_dimensions"]
        if page_no >= len(dims):
            return json.dumps({"error": f"Page {page_no} not found"})

        pw, ph = dims[page_no]

        if x0 < 0 or y0 < 0 or (x0 + text_width) > pw or (y0 + text_height) > ph:
            return json.dumps({
                "is_valid": False,
                "reason"  : "Outside page boundaries",
            })

        probe = shapely_box(
            x0 - 5, y0 - 5,
            x0 + text_width + 5,
            y0 + text_height + 5
        )

        draw_conflicts = [
            list(b) for b in _session_state["all_drawing_bboxes"]
            if probe.intersects(shapely_box(*b))
        ]

        committed_conflicts = [
            p["text"]
            for p in _session_state["committed_positions"]
            if p.get("page_no") == page_no
            and probe.intersects(shapely_box(*p["new_bbox"]))
        ]

        is_valid = (
            len(draw_conflicts) == 0 and
            len(committed_conflicts) == 0
        )

        return json.dumps({
            "is_valid"              : is_valid,
            "proposed_bbox"         : [x0, y0, x0 + text_width, y0 + text_height],
            "drawing_conflicts"     : draw_conflicts[:5],
            "text_conflicts"        : committed_conflicts[:5],
            "recommendation"        : "ACCEPT" if is_valid else "REJECT",
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
    Save a repositioning decision so the PDF can be rebuilt.
    Call this after validate_position returns is_valid=true.

    Args:
        page_no      : Page number (0-indexed)
        text         : The text content to move
        original_bbox: Original [x0, y0, x1, y1]
        new_x0       : New left edge
        new_y0       : New top edge
        font_size    : Font size (default 8.0)
        color        : RGB list 0-1 range (default black)
    """
    try:
        if color is None:
            color = [0, 0, 0]

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

        logger.info(f"Committed: '{text}' {orig} → {new_bbox}")

        return json.dumps({
            "status"      : "committed",
            "text"        : text,
            "moved_from"  : orig,
            "moved_to"    : new_bbox,
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool list for agent ───────────────────────────────────────────────────────
ALL_TOOLS = [
    get_page_context,
    analyze_free_spaces,
    validate_position,
    commit_repositioning,
]