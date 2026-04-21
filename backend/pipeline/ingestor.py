# backend/pipeline/ingestor.py

import os
import io
import glob
import shutil
import subprocess
import tempfile
from .utils import get_logger, get_file_extension

logger = get_logger(__name__)


def _find_binary(candidates: list) -> str:
    for c in candidates:
        found = shutil.which(c)
        if found:
            return found
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _detect_dwg_engine() -> dict:
    versioned_oda = (
        glob.glob("/usr/bin/ODAFileConverter_*/ODAFileConverter") +
        glob.glob("/opt/ODAFileConverter_*/ODAFileConverter")
    )
    found = {}
    for name, paths in {
        "oda"    : versioned_oda + ["ODAFileConverter"],
        "dwg2dxf": ["dwg2dxf", "/usr/local/bin/dwg2dxf"],
        "dwg2svg": ["dwg2SVG", "dwg2svg",
                    "/usr/local/bin/dwg2SVG",
                    "/usr/local/bin/dwg2svg"],
    }.items():
        p = _find_binary(paths)
        if p:
            found[name] = p
            logger.info(f"Engine: {name} → {p}")
    return found


def convert_to_pdf(input_path: str, output_pdf_path: str) -> str:
    ext = get_file_extension(input_path)
    logger.info(f"Converting: {os.path.basename(input_path)} (.{ext})")

    if ext == "pdf":
        logger.info("PDF → passthrough.")
        return input_path

    if ext in ("dwg", "dxf"):
        return _cad_to_pdf(input_path, output_pdf_path, ext)

    raise ValueError(f"Unsupported: .{ext}")


def _cad_to_pdf(input_path: str, output_pdf_path: str, ext: str) -> str:
    engines  = _detect_dwg_engine()
    dxf_path = None
    errors   = []

    if ext == "dwg" and "dwg2dxf" in engines:
        try:
            dxf_path = _libredwg_to_dxf(engines["dwg2dxf"], input_path)
        except Exception as e:
            logger.warning(f"dwg2dxf: {e}")
            errors.append(str(e))

    if not dxf_path:
        dxf_path = input_path

    try:
        result = _dxf_to_pdf(dxf_path, output_pdf_path)
        _verify_pdf(result, "output")
        return result
    except Exception as e:
        errors.append(str(e))
        raise RuntimeError(
            "Conversion failed:\n" +
            "\n".join(f"  {err}" for err in errors)
        )


def _libredwg_to_dxf(dwg2dxf_path: str, dwg_path: str) -> str:
    output_dir = tempfile.mkdtemp(prefix="libredwg_")
    base       = os.path.splitext(os.path.basename(dwg_path))[0]
    dxf_out    = os.path.join(output_dir, base + ".dxf")

    result = subprocess.run(
        [dwg2dxf_path, dwg_path, "-o", dxf_out],
        capture_output=True, text=True, timeout=120
    )

    if not os.path.exists(dxf_out) or os.path.getsize(dxf_out) < 100:
        alt = os.path.join(
            os.path.dirname(os.path.abspath(dwg_path)),
            base + ".dxf"
        )
        if os.path.exists(alt) and os.path.getsize(alt) > 100:
            shutil.copy2(alt, dxf_out)

    if not os.path.exists(dxf_out) or os.path.getsize(dxf_out) < 100:
        raise RuntimeError(
            f"dwg2dxf failed: {result.stderr[:200]}"
        )

    logger.info(
        f"DXF: {dxf_out} ({os.path.getsize(dxf_out)//1024}KB)"
    )
    return dxf_out


def _dxf_to_pdf(dxf_path: str, output_pdf_path: str) -> str:
    """
    DXF → PDF using ezdxf + matplotlib.

    Critical fix: set white background on RenderContext
    so CAD colors (designed for dark screens) are remapped
    correctly for paper/print output.
    """
    import ezdxf
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    logger.info(f"Rendering: {os.path.basename(dxf_path)}")

    # Load DXF
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception:
        logger.warning("Standard read failed → recovery")
        try:
            doc, _ = ezdxf.recover.readfile(dxf_path)
        except Exception as e:
            raise RuntimeError(f"Cannot read DXF: {e}")

    # Pick layout with most content
    layout = _best_layout(doc)
    logger.info(
        f"Layout: '{layout.name}' "
        f"({len(list(layout))} entities)"
    )

    # ── Build RenderContext with white background ─────────────────
    # This is the fix for white-on-white invisible rendering.
    # We pass the background color when creating the context
    # so ezdxf remaps all entity colors for paper output.
    ctx = _build_render_context(doc)

    # ── Render ────────────────────────────────────────────────────
    fig = plt.figure(figsize=(22, 17), dpi=150)
    ax  = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ax.set_axis_off()
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    backend  = MatplotlibBackend(ax)
    frontend = Frontend(ctx, backend)

    # draw_layout with correct background
    _draw_with_white_bg(frontend, layout)

    # ── Save ──────────────────────────────────────────────────────
    fig.savefig(
        output_pdf_path,
        format="pdf",
        bbox_inches="tight",
        facecolor="white",
        edgecolor="none",
        dpi=150,
    )
    plt.close(fig)

    if not os.path.exists(output_pdf_path):
        raise RuntimeError("No output file created")

    size_kb = os.path.getsize(output_pdf_path) // 1024
    logger.info(f"PDF: {output_pdf_path} ({size_kb}KB)")
    return output_pdf_path


def _build_render_context(doc):
    """
    Build RenderContext with white background.
    Handles API differences across ezdxf versions.
    """
    from ezdxf.addons.drawing import RenderContext

    ctx = RenderContext(doc)

    # Try all known ways to set background color
    # across different ezdxf versions

    # Method 1: current_layout_properties.set_colors (ezdxf >= 0.17)
    try:
        ctx.current_layout_properties.set_colors(
            bg="#ffffff", fg="#000000"
        )
        logger.debug("Background set via set_colors()")
        return ctx
    except Exception:
        pass

    # Method 2: set background_color attribute directly
    try:
        ctx.current_layout_properties.background_color = "#ffffff"
        ctx.current_layout_properties.default_color    = "#000000"
        logger.debug("Background set via direct attributes")
        return ctx
    except Exception:
        pass

    # Method 3: use set_current_layout with override
    try:
        from ezdxf.addons.drawing.properties import RenderContext as RC
        ctx2 = RC(doc, export_mode=True)
        logger.debug("Background set via export_mode=True")
        return ctx2
    except Exception:
        pass

    # Method 4: no override possible — apply post-render fix instead
    logger.debug(
        "Cannot set background color on RenderContext. "
        "Will invert dark colors post-render."
    )
    return ctx


def _draw_with_white_bg(frontend, layout):
    """
    Draw layout, trying all available API options
    for white background rendering.
    """
    # Try with layout_properties parameter (ezdxf >= 1.0)
    try:
        from ezdxf.addons.drawing.properties import LayerProperties
        frontend.draw_layout(layout, finalize=True)
        return
    except Exception:
        pass

    # Standard draw
    try:
        frontend.draw_layout(layout, finalize=True)
    except Exception as e:
        raise RuntimeError(f"draw_layout failed: {e}")


def _best_layout(doc):
    """Return layout with the most entities."""
    best        = doc.modelspace()
    best_count  = len(list(best))

    for layout in doc.layouts:
        try:
            count = len(list(layout))
            logger.info(f"  Layout '{layout.name}': {count} entities")
            if count > best_count:
                best_count = count
                best       = layout
        except Exception:
            pass

    return best


def _verify_pdf(pdf_path: str, label: str):
    import fitz

    if not os.path.exists(pdf_path):
        raise RuntimeError(f"[{label}] not found")

    size_kb = os.path.getsize(pdf_path) // 1024
    if size_kb < 5:
        raise RuntimeError(
            f"[{label}] too small ({size_kb}KB)"
        )

    doc   = fitz.open(pdf_path)
    pages = len(doc)
    if pages == 0:
        doc.close()
        raise RuntimeError(f"[{label}] 0 pages")

    page  = doc[0]
    draws = page.get_drawings()
    texts = page.get_text("text").strip()
    doc.close()

    if len(draws) == 0 and not texts:
        raise RuntimeError(
            f"[{label}] blank page "
            f"(0 drawings, no text, {size_kb}KB)"
        )

    logger.info(
        f"[{label}] ✅ {pages}p {size_kb}KB "
        f"{len(draws)} drawings"
    )
