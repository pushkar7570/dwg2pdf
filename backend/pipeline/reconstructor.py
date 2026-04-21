# backend/pipeline/reconstructor.py

import fitz
from typing import List
from .utils import get_logger, clamp

logger = get_logger(__name__)

MAX_COVER_W_FRACTION = 0.15
MAX_COVER_H_FRACTION = 0.10


def rebuild_pdf(
    original_pdf_path : str,
    repositioned_items: List[dict],
    output_path       : str,
) -> str:
    """
    Non-destructive PDF rebuild using page-clone approach.

    Method:
    1. Open original PDF as READ-ONLY
    2. Create new empty output PDF
    3. For each page:
       a. Clone original page into output via show_pdf_page()
          → ALL drawing content copied faithfully, zero modification
       b. For each repositioned text item:
          → Safety-check bbox size (reject oversized cover areas)
          → Sample background brightness at original position
          → If white background: draw small white cover over old text
          → Insert text at new position with preserved style
    4. Save output PDF

    The original PDF is NEVER modified.
    Drawing content is ALWAYS preserved via show_pdf_page().
    """
    logger.info(
        f"Rebuilding: {len(repositioned_items)} item(s) → {output_path}"
    )

    by_page: dict = {}
    for item in repositioned_items:
        by_page.setdefault(item.get("page_no", 0), []).append(item)

    src = fitz.open(original_pdf_path)
    out = fitz.open()

    for page_no in range(len(src)):
        src_page = src[page_no]
        w        = src_page.rect.width
        h        = src_page.rect.height

        # Create new page same size
        new_page = out.new_page(width=w, height=h)

        # Clone original content (drawing intact)
        new_page.show_pdf_page(
            new_page.rect,
            src,
            page_no,
            keep_proportion=True,
            overlay=False,
        )

        # Apply text fixes
        for item in by_page.get(page_no, []):
            try:
                _apply_text_fix(new_page, src_page, item, w, h)
            except Exception as e:
                logger.error(
                    f"Fix failed for '{item.get('text','?')}': {e}"
                )

    src.close()
    out.save(output_path, garbage=4, deflate=True)
    out.close()

    logger.info(f"Saved: {output_path}")
    return output_path


def _apply_text_fix(
    new_page: fitz.Page,
    src_page: fitz.Page,
    item    : dict,
    pw      : float,
    ph      : float,
):
    """Apply a single text repositioning fix."""
    text      = item.get("text", "")
    orig_bbox = item.get("original_bbox", [])
    new_bbox  = item.get("new_bbox", [])
    font_size = max(4.0, float(item.get("font_size", 8.0)))
    color     = item.get("color", [0, 0, 0])
    rotation  = float(item.get("rotation", 0.0))

    # Validate both bboxes
    if not _valid_bbox(orig_bbox, pw, ph):
        logger.warning(f"  Invalid orig_bbox for '{text}': {orig_bbox}")
        return
    if not _valid_bbox(new_bbox, pw, ph):
        logger.warning(f"  Invalid new_bbox for '{text}': {new_bbox}")
        return

    safe_color = tuple(
        clamp(float(c), 0.0, 1.0)
        for c in (color[:3] if len(color) >= 3 else [0, 0, 0])
    )

    orig_rect = fitz.Rect(orig_bbox)
    cw        = orig_rect.width
    ch        = orig_rect.height

    # ── Cover original text (only if small enough + white bg) ────
    too_large = (
        cw > pw * MAX_COVER_W_FRACTION or
        ch > ph * MAX_COVER_H_FRACTION
    )

    if not too_large and cw > 0.5 and ch > 0.5:
        if _bg_is_white(src_page, orig_rect):
            cover = fitz.Rect(
                orig_rect.x0 - 1, orig_rect.y0 - 1,
                orig_rect.x1 + 1, orig_rect.y1 + 1,
            ) & new_page.rect

            if not cover.is_empty:
                new_page.draw_rect(
                    cover,
                    color=(1, 1, 1),
                    fill=(1, 1, 1),
                    width=0,
                    overlay=True,
                )
                logger.debug(f"  Covered '{text}' at {orig_bbox}")
        else:
            logger.debug(
                f"  Dark bg at '{text}' orig pos "
                f"— skipping cover (drawing preserved)"
            )
    else:
        logger.debug(
            f"  Skipped cover for '{text}': "
            f"too large ({cw:.0f}x{ch:.0f}) or zero size"
        )

    # ── Insert text at new position ───────────────────────────────
    nx = clamp(new_bbox[0], 2.0, pw - 5.0)
    ny = clamp(new_bbox[3], 5.0, ph - 2.0)

    if abs(rotation) < 1.0:
        new_page.insert_text(
            fitz.Point(nx, ny),
            text,
            fontsize=font_size,
            color=safe_color,
            fontname="helv",
            overlay=True,
        )
    else:
        tb = fitz.Rect(new_bbox)
        if not tb.is_empty and tb.width > 1 and tb.height > 1:
            new_page.insert_textbox(
                tb,
                text,
                fontsize=font_size,
                color=safe_color,
                fontname="helv",
                rotate=int(rotation),
                overlay=True,
            )

    logger.debug(
        f"  Placed '{text}' at "
        f"({nx:.0f},{ny:.0f})"
    )


def _valid_bbox(bbox, pw: float, ph: float) -> bool:
    """Validate a bbox is sane for this page size."""
    try:
        x0, y0, x1, y1 = bbox
        if x1 <= x0 or y1 <= y0:
            return False
        tol = 50
        if x0 < -tol or y0 < -tol or x1 > pw+tol or y1 > ph+tol:
            return False
        area = (x1 - x0) * (y1 - y0)
        if pw * ph > 0 and area / (pw * ph) > 0.8:
            return False
        return True
    except Exception:
        return False


def _bg_is_white(
    page     : fitz.Page,
    bbox     : fitz.Rect,
    threshold: float = 0.82,
) -> bool:
    """Sample average pixel brightness under bbox."""
    try:
        clip = bbox & page.rect
        if clip.is_empty or clip.width < 1 or clip.height < 1:
            return True

        pix     = page.get_pixmap(
            matrix=fitz.Matrix(1, 1),
            clip=clip,
            alpha=False,
        )
        samples = pix.samples
        n_px    = pix.width * pix.height

        if n_px == 0:
            return True

        total = sum(
            (samples[i] + samples[i+1] + samples[i+2]) / (3 * 255.0)
            for i in range(0, len(samples) - 2, 3)
        )
        return (total / n_px) > threshold

    except Exception:
        return True
