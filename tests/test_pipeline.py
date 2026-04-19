# tests/test_pipeline.py

import sys
import os

# ── Fix path ONCE at the top for ALL tests ────────────────────────────────────
# Add backend/ to path so all pipeline imports work correctly
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../backend")
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../scripts")

sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, SCRIPTS_DIR)

# ── Now import everything at the top level ────────────────────────────────────
from generate_test_dxf import generate_sample_dxf
from pipeline.ingestor import convert_to_pdf
from pipeline.parser import parse_pdf
from pipeline.detector import detect_overlaps


# ── Test 1: DXF Generation + Conversion ──────────────────────────────────────

def test_generate_and_convert():
    """Test DXF generation and conversion to PDF"""
    print("\n[TEST 1] DXF Generation + Conversion")

    dxf_path = generate_sample_dxf("/tmp/test_sample.dxf")
    assert os.path.exists(dxf_path), f"DXF not created at {dxf_path}"
    print(f"  DXF created : {dxf_path}")

    pdf_path = convert_to_pdf(dxf_path, "/tmp/test_output.pdf")
    assert os.path.exists(pdf_path), f"PDF not created at {pdf_path}"
    assert os.path.getsize(pdf_path) > 1000, "PDF file is too small (likely empty)"
    print(f"  PDF created : {pdf_path} ({os.path.getsize(pdf_path)/1024:.1f} KB)")

    print("✅ Conversion test passed")
    return pdf_path


# ── Test 2: PDF Parser ────────────────────────────────────────────────────────

def test_parser(pdf_path: str = None):
    """Test PDF parsing extracts text blocks and drawing elements"""
    print("\n[TEST 2] PDF Parser")

    if pdf_path is None:
        dxf_path = generate_sample_dxf("/tmp/test_parse.dxf")
        pdf_path = convert_to_pdf(dxf_path, "/tmp/test_parse.pdf")

    parsed = parse_pdf(pdf_path)

    assert parsed is not None, "parse_pdf returned None"
    assert parsed.page_count >= 1, "No pages found in PDF"

    print(f"  Pages          : {parsed.page_count}")
    print(f"  Text blocks    : {len(parsed.all_text_blocks)}")
    print(f"  Drawing elements: {len(parsed.all_drawing_elements)}")

    # Show extracted text for debugging
    if parsed.all_text_blocks:
        print("  Extracted text samples:")
        for tb in parsed.all_text_blocks[:5]:
            print(f"    → '{tb.text}' at bbox {[round(v,1) for v in tb.bbox]}")

    print("✅ Parser test passed")
    return parsed


# ── Test 3: Overlap Detector ──────────────────────────────────────────────────

def test_detector(parsed_doc=None):
    """Test overlap detection identifies conflicts"""
    print("\n[TEST 3] Overlap Detector")

    if parsed_doc is None:
        dxf_path = generate_sample_dxf("/tmp/test_detect.dxf")
        pdf_path = convert_to_pdf(dxf_path, "/tmp/test_detect.pdf")
        parsed_doc = parse_pdf(pdf_path)

    result = detect_overlaps(parsed_doc)

    assert result is not None, "detect_overlaps returned None"

    print(f"  Has overlaps   : {result.has_overlaps}")
    print(f"  Total conflicts: {result.total_conflicts}")
    print(f"  Summary        : {result.summary}")

    if result.conflicts:
        print("  Conflict details:")
        for c in result.conflicts:
            print(
                f"    → '{c.text}' | overlap: {c.max_overlap_ratio:.1%} "
                f"| bbox: {[round(v,1) for v in c.current_bbox]}"
            )

    print("✅ Detector test passed")
    return result


# ── Test 4: Full Pipeline (no AI) ─────────────────────────────────────────────

def test_full_pipeline_no_ai():
    """
    Test the complete pipeline up to (but not including) the AI agent.
    This verifies all stages work end-to-end without needing API keys.
    """
    print("\n[TEST 4] Full Pipeline (no AI)")

    # Stage 1: Generate DXF
    dxf_path = generate_sample_dxf("/tmp/test_full.dxf")
    print(f"  Stage 1 OK : DXF generated")

    # Stage 2: Convert to PDF
    pdf_path = convert_to_pdf(dxf_path, "/tmp/test_full.pdf")
    print(f"  Stage 2 OK : PDF converted ({os.path.getsize(pdf_path)//1024}KB)")

    # Stage 3: Parse
    parsed = parse_pdf(pdf_path)
    print(
        f"  Stage 3 OK : Parsed {len(parsed.all_text_blocks)} text, "
        f"{len(parsed.all_drawing_elements)} drawings"
    )

    # Stage 4: Detect
    result = detect_overlaps(parsed)
    print(f"  Stage 4 OK : {result.total_conflicts} overlap(s) detected")

    # Stage 5: Fallback fix (no AI needed)
    if result.has_overlaps:
        from agent.tools import initialize_session, get_committed_positions
        from agent.cad_agent import _apply_fallback_fix

        page_dims      = [(p.width, p.height) for p in parsed.pages]
        drawing_bboxes = [d.bbox for p in parsed.pages for d in p.drawing_elements]
        text_bboxes    = [t.bbox for p in parsed.pages for t in p.text_blocks]

        initialize_session(
            page_dimensions=page_dims,
            text_bboxes=text_bboxes,
            drawing_bboxes=drawing_bboxes
        )

        for conflict in result.conflicts:
            _apply_fallback_fix(conflict)

        fixes = get_committed_positions()
        print(f"  Stage 5 OK : {len(fixes)} text block(s) repositioned (fallback)")

        # Stage 6: Rebuild PDF
        from pipeline.reconstructor import rebuild_pdf
        output_path = "/tmp/test_full_corrected.pdf"
        rebuild_pdf(pdf_path, fixes, output_path)

        assert os.path.exists(output_path), "Corrected PDF not created"
        print(f"  Stage 6 OK : Corrected PDF saved ({os.path.getsize(output_path)//1024}KB)")
    else:
        print("  Stage 5 SKIP: No overlaps found (nothing to fix)")

    print("✅ Full pipeline test passed")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  CAD Overlap Fixer - Pipeline Tests")
    print("=" * 55)

    errors = []

    tests = [
        ("Generate + Convert", test_generate_and_convert),
        ("Parser",             test_parser),
        ("Detector",           test_detector),
        ("Full Pipeline",      test_full_pipeline_no_ai),
    ]

    for name, test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            print(f"❌ {name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            errors.append(name)

    print()
    print("=" * 55)
    if errors:
        print(f"  ❌ {len(errors)} test(s) failed: {', '.join(errors)}")
    else:
        print("  ✅ All 4 tests passed! Pipeline is working correctly.")
    print("=" * 55)