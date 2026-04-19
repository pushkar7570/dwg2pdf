# backend/pipeline/parser.py

import fitz  # PyMuPDF
from dataclasses import dataclass, field
from typing import List
from .utils import get_logger

logger = get_logger(__name__)


@dataclass
class TextBlock:
    """Represents a text element extracted from PDF"""
    text: str
    bbox: tuple
    page_no: int
    font_size: float = 8.0
    font_name: str = "helv"
    color: tuple = (0, 0, 0)
    block_id: str = ""

    def __post_init__(self):
        if not self.block_id:
            self.block_id = f"p{self.page_no}_{int(self.bbox[0])}_{int(self.bbox[1])}"

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def center(self) -> tuple:
        return (
            (self.bbox[0] + self.bbox[2]) / 2,
            (self.bbox[1] + self.bbox[3]) / 2
        )


@dataclass
class DrawingElement:
    """Represents a drawing path/shape extracted from PDF"""
    element_type: str
    bbox: tuple
    page_no: int

    @property
    def area(self) -> float:
        return (
            max(0, self.bbox[2] - self.bbox[0]) *
            max(0, self.bbox[3] - self.bbox[1])
        )


@dataclass
class ParsedPage:
    """All elements from a single PDF page"""
    page_no: int
    width: float
    height: float
    text_blocks: List[TextBlock] = field(default_factory=list)
    drawing_elements: List[DrawingElement] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """Complete parsed PDF document"""
    pdf_path: str
    pages: List[ParsedPage] = field(default_factory=list)

    @property
    def all_text_blocks(self) -> List[TextBlock]:
        return [t for page in self.pages for t in page.text_blocks]

    @property
    def all_drawing_elements(self) -> List[DrawingElement]:
        return [d for page in self.pages for d in page.drawing_elements]

    @property
    def page_count(self) -> int:
        return len(self.pages)


def parse_pdf(pdf_path: str) -> ParsedDocument:
    """
    Parse PDF and extract all text blocks and drawing elements
    with their exact bounding box coordinates.
    """
    logger.info(f"Parsing PDF: {pdf_path}")
    doc_data = ParsedDocument(pdf_path=pdf_path)

    try:
        pdf_doc = fitz.open(pdf_path)

        for page_no in range(len(pdf_doc)):
            page = pdf_doc[page_no]
            rect = page.rect

            parsed_page = ParsedPage(
                page_no=page_no,
                width=rect.width,
                height=rect.height
            )

            # ── Extract Text Blocks ──────────────────────────────
            text_dict = page.get_text(
                "dict",
                flags=fitz.TEXT_PRESERVE_WHITESPACE
            )

            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue

                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        raw_text = span.get("text", "").strip()
                        if not raw_text:
                            continue

                        bbox = tuple(span.get("bbox", (0, 0, 0, 0)))

                        # Skip zero-area text
                        if bbox[2] - bbox[0] < 1 or bbox[3] - bbox[1] < 1:
                            continue

                        # Decode color
                        raw_color = span.get("color", 0)
                        if isinstance(raw_color, int):
                            r = ((raw_color >> 16) & 0xFF) / 255.0
                            g = ((raw_color >> 8)  & 0xFF) / 255.0
                            b = ( raw_color        & 0xFF) / 255.0
                            color = (r, g, b)
                        else:
                            color = (0.0, 0.0, 0.0)

                        text_block = TextBlock(
                            text=raw_text,
                            bbox=bbox,
                            page_no=page_no,
                            font_size=span.get("size", 8.0),
                            font_name=span.get("font", "helv"),
                            color=color
                        )
                        parsed_page.text_blocks.append(text_block)

            # ── Extract Drawing Elements ─────────────────────────
            paths = page.get_drawings()
            for path in paths:
                path_rect = path.get("rect")
                if not path_rect:
                    continue

                bbox = (
                    path_rect.x0,
                    path_rect.y0,
                    path_rect.x1,
                    path_rect.y1
                )

                # Skip tiny noise elements
                if (bbox[2] - bbox[0]) < 2 and (bbox[3] - bbox[1]) < 2:
                    continue

                drawing_elem = DrawingElement(
                    element_type=str(path.get("type", "path")),
                    bbox=bbox,
                    page_no=page_no
                )
                parsed_page.drawing_elements.append(drawing_elem)

            logger.info(
                f"  Page {page_no}: "
                f"{len(parsed_page.text_blocks)} text blocks, "
                f"{len(parsed_page.drawing_elements)} drawing elements"
            )
            doc_data.pages.append(parsed_page)

        pdf_doc.close()

    except Exception as e:
        logger.error(f"PDF parsing failed: {str(e)}")
        raise RuntimeError(f"Failed to parse PDF: {str(e)}")

    return doc_data
