# backend/pipeline/ingestor.py

import os
import ezdxf
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for server
import matplotlib.pyplot as plt
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from .utils import get_logger, get_file_extension

logger = get_logger(__name__)


def convert_to_pdf(input_path: str, output_pdf_path: str) -> str:
    """
    Master converter: handles DWG, DXF, or passes PDF through.
    Returns path to PDF file.
    """
    ext = get_file_extension(input_path)
    logger.info(f"Converting file type: {ext} → PDF")

    if ext == "pdf":
        logger.info("File is already PDF, using directly.")
        return input_path

    elif ext in ("dxf", "dwg"):
        return _dxf_to_pdf(input_path, output_pdf_path)

    else:
        raise ValueError(f"Unsupported file type: {ext}. Use DWG, DXF, or PDF.")


def _dxf_to_pdf(dxf_path: str, output_pdf_path: str) -> str:
    """
    Convert DXF/DWG file to PDF using ezdxf + matplotlib.
    
    NOTE on DWG: ezdxf supports DXF natively. Many DWG files
    can be read directly as ezdxf attempts DWG parsing,
    but DXF is the guaranteed open format.
    """
    try:
        logger.info(f"Reading DXF/DWG: {dxf_path}")

        # Try reading - works for DXF directly, may work for some DWG
        try:
            doc = ezdxf.readfile(dxf_path)
        except ezdxf.DXFStructureError:
            # Try recover mode for damaged/complex files
            logger.warning("Standard read failed, attempting recovery mode...")
            doc, auditor = ezdxf.recover.readfile(dxf_path)
            if auditor.has_errors:
                logger.warning(f"File has {len(auditor.errors)} structural errors (continuing anyway)")

        msp = doc.modelspace()

        # Set up matplotlib figure with good resolution
        fig = plt.figure(figsize=(24, 18), dpi=150)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_aspect("equal")

        # Render the CAD drawing
        ctx = RenderContext(doc)
        backend = MatplotlibBackend(ax)
        frontend = Frontend(ctx, backend)
        frontend.draw_layout(msp, finalize=True)

        # Remove axes decorations for clean PDF
        ax.set_axis_off()

        logger.info(f"Saving PDF to: {output_pdf_path}")
        fig.savefig(
            output_pdf_path,
            format="pdf",
            bbox_inches="tight",
            pad_inches=0.1,
            facecolor="white"
        )
        plt.close(fig)

        if not os.path.exists(output_pdf_path):
            raise RuntimeError("PDF file was not created")

        logger.info(f"Conversion successful: {output_pdf_path}")
        return output_pdf_path

    except Exception as e:
        plt.close("all")
        logger.error(f"DXF→PDF conversion failed: {str(e)}")
        raise RuntimeError(f"Failed to convert CAD file to PDF: {str(e)}")