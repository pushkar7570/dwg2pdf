# backend/pipeline/reconstructor.py

import fitz  # PyMuPDF
from typing import List
from .utils import get_logger, clamp

logger = get_logger(__name__)


def rebuild_pdf(
    original_pdf_path: str,
    repositioned_items: List[dict],
    output_path: str
) -> str:
    """
    Rebuild PDF with text moved to new positions.
    
    Steps per text block:
    1. Redact (white-out) original overlapping position
    2. Insert text at AI-suggested new position
    3. Preserve font size and color as much as possible
    """
    logger.info(f"Rebuilding PDF with {len(repositioned_items)} repositioned items")

    doc = fitz.open(original_pdf_path)

    # Group by page for efficiency
    pages_to_fix: dict[int, List[dict]] = {}
    for item in repositioned_items:
        pn = item["page_no"]
        pages_to_fix.setdefault(pn, []).append(item)

    for page_no, items in pages_to_fix.items():
        if page_no >= len(doc):
            logger.warning(f"Page {page_no} out of range, skipping")
            continue

        page = doc[page_no]
        page_rect = page.rect

        for item in items:
            try:
                orig_bbox = fitz.Rect(item["original_bbox"])
                new_bbox = item["new_bbox"]
                text = item["text"]
                font_size = item.get("font_size", 8.0)
                color = item.get("color", (0, 0, 0))

                # ── Step 1: Redact original position ────────────
                # Add slight padding to ensure full text coverage
                padded_orig = fitz.Rect(
                    orig_bbox.x0 - 1,
                    orig_bbox.y0 - 1,
                    orig_bbox.x1 + 1,
                    orig_bbox.y1 + 1
                )
                redact = page.add_redact_annot(
                    padded_orig,
                    fill=(1, 1, 1),  # White fill
                    text=""
                )

                # ── Step 2: Apply redaction ──────────────────────
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

                # ── Step 3: Insert at new position ──────────────
                # Clamp to page boundaries
                new_x = clamp(new_bbox[0], 5, page_rect.width - 50)
                new_y = clamp(new_bbox[3], 10, page_rect.height - 5)

                # Ensure color values are valid floats 0-1
                safe_color = tuple(clamp(float(c), 0.0, 1.0) for c in color[:3])

                page.insert_text(
                    point=fitz.Point(new_x, new_y),
                    text=text,
                    fontsize=max(6.0, min(font_size, 72.0)),
                    color=safe_color,
                    fontname="helv",  # Helvetica - universally available
                    overlay=True
                )

                logger.debug(
                    f"  Moved '{text}': {item['original_bbox']} → {new_bbox}"
                )

            except Exception as e:
                logger.error(f"  Failed to reposition '{item.get('text', '?')}': {e}")
                # Continue with other items - don't fail entire rebuild

    doc.save(output_path, garbage=4, deflate=True, clean=True)
    doc.close()

    logger.info(f"Rebuilt PDF saved: {output_path}")
    return output_path