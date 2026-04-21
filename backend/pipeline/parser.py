# backend/pipeline/parser.py

import math
import fitz
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from .utils import get_logger

logger = get_logger(__name__)


@dataclass
class TextBlock:
    text     : str
    bbox     : tuple
    page_no  : int
    font_size: float          = 8.0
    font_name: str            = "helv"
    color    : tuple          = (0, 0, 0)
    rotation : float          = 0.0
    block_id : str            = ""

    def __post_init__(self):
        if not self.block_id:
            self.block_id = (
                f"p{self.page_no}_"
                f"{int(self.bbox[0])}_"
                f"{int(self.bbox[1])}"
            )

    @property
    def width(self)  -> float:
        return max(0.0, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        return max(0.0, self.bbox[3] - self.bbox[1])

    @property
    def center(self) -> Tuple[float, float]:
        return (
            (self.bbox[0] + self.bbox[2]) / 2.0,
            (self.bbox[1] + self.bbox[3]) / 2.0,
        )


@dataclass
class DrawingElement:
    element_type: str
    bbox        : tuple
    page_no     : int
    is_filled   : bool            = False
    fill_color  : Optional[tuple] = None

    @property
    def area(self) -> float:
        return (
            max(0.0, self.bbox[2] - self.bbox[0]) *
            max(0.0, self.bbox[3] - self.bbox[1])
        )


@dataclass
class ParsedPage:
    page_no         : int
    width           : float
    height          : float
    text_blocks     : List[TextBlock]      = field(default_factory=list)
    drawing_elements: List[DrawingElement] = field(default_factory=list)


@dataclass
class ParsedDocument:
    pdf_path: str
    pages   : List[ParsedPage] = field(default_factory=list)

    @property
    def all_text_blocks(self) -> List[TextBlock]:
        return [t for p in self.pages for t in p.text_blocks]

    @property
    def all_drawing_elements(self) -> List[DrawingElement]:
        return [d for p in self.pages for d in p.drawing_elements]

    @property
    def page_count(self) -> int:
        return len(self.pages)


def parse_pdf(pdf_path: str) -> ParsedDocument:
    """
    Extract text blocks and drawing elements from PDF.

    Handles:
    - Real CAD PDFs (from ODA, AutoCAD export)
    - SVG-rendered PDFs (from svglib)
    - Complex multi-layer drawings

    Safety filters:
    - Skips zero-area text spans
    - Skips oversized bboxes (garbage extractions)
    - Clips bboxes to page bounds
    """
    logger.info(f"Parsing PDF: {pdf_path}")
    doc_data = ParsedDocument(pdf_path=pdf_path)

    try:
        pdf_doc = fitz.open(pdf_path)

        for page_no in range(len(pdf_doc)):
            page = pdf_doc[page_no]
            rect = page.rect
            pw   = rect.width
            ph   = rect.height

            parsed_page = ParsedPage(
                page_no=page_no,
                width=pw,
                height=ph,
            )

            # ── Extract text ─────────────────────────────────────
            try:
                text_dict = page.get_text(
                    "dict",
                    flags=fitz.TEXT_PRESERVE_WHITESPACE,
                )

                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue

                    for line in block.get("lines", []):
                        direction = line.get("dir", (1, 0))
                        try:
                            rotation = math.degrees(
                                math.atan2(direction[1], direction[0])
                            )
                        except Exception:
                            rotation = 0.0

                        for span in line.get("spans", []):
                            raw = span.get("text", "").strip()
                            if not raw:
                                continue

                            bbox = tuple(span.get("bbox", (0, 0, 0, 0)))
                            bw   = bbox[2] - bbox[0]
                            bh   = bbox[3] - bbox[1]

                            # Skip zero / negative size
                            if bw < 0.5 or bh < 0.5:
                                continue

                            # Skip oversized (corrupt extractions)
                            if bw > pw * 0.5 or bh > ph * 0.5:
                                logger.debug(
                                    f"Skipped oversized bbox "
                                    f"'{raw[:20]}': "
                                    f"{bw:.0f}x{bh:.0f}"
                                )
                                continue

                            # Clip to page with tolerance
                            tol  = 10.0
                            bbox = (
                                max(bbox[0], -tol),
                                max(bbox[1], -tol),
                                min(bbox[2], pw + tol),
                                min(bbox[3], ph + tol),
                            )

                            # Decode color
                            raw_color = span.get("color", 0)
                            if isinstance(raw_color, int):
                                color = (
                                    ((raw_color >> 16) & 0xFF) / 255.0,
                                    ((raw_color >>  8) & 0xFF) / 255.0,
                                    ( raw_color        & 0xFF) / 255.0,
                                )
                            else:
                                color = (0.0, 0.0, 0.0)

                            parsed_page.text_blocks.append(TextBlock(
                                text=raw,
                                bbox=bbox,
                                page_no=page_no,
                                font_size=float(span.get("size", 8.0)),
                                font_name=str(span.get("font", "helv")),
                                color=color,
                                rotation=rotation,
                            ))

            except Exception as e:
                logger.warning(
                    f"Text extraction error page {page_no}: {e}"
                )

            # ── Extract drawings ─────────────────────────────────
            try:
                for path in page.get_drawings():
                    r = path.get("rect")
                    if not r:
                        continue

                    bbox = (r.x0, r.y0, r.x1, r.y1)

                    if (bbox[2]-bbox[0]) < 0.5 and (bbox[3]-bbox[1]) < 0.5:
                        continue

                    fill      = path.get("fill", None)
                    is_filled = (
                        fill is not None
                        and fill != (1, 1, 1)
                        and fill != 1
                    )

                    parsed_page.drawing_elements.append(DrawingElement(
                        element_type=str(path.get("type", "path")),
                        bbox=bbox,
                        page_no=page_no,
                        is_filled=is_filled,
                        fill_color=tuple(fill) if is_filled and fill else None,
                    ))

            except Exception as e:
                logger.warning(
                    f"Drawing extraction error page {page_no}: {e}"
                )

            logger.info(
                f"  Page {page_no}: "
                f"{len(parsed_page.text_blocks)} text, "
                f"{len(parsed_page.drawing_elements)} drawings "
                f"({sum(1 for d in parsed_page.drawing_elements if d.is_filled)} filled)"
            )
            doc_data.pages.append(parsed_page)

        pdf_doc.close()

    except Exception as e:
        raise RuntimeError(f"PDF parse failed: {e}")

    return doc_data
