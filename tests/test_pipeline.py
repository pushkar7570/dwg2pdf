# tests/test_pipeline.py

import sys, os

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../backend")
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../scripts")
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import fitz
from generate_test_dxf      import generate_sample_dxf, generate_sample_pdf_with_text
from pipeline.ingestor      import convert_to_pdf, _detect_dwg_engine
from pipeline.parser        import parse_pdf
from pipeline.detector      import detect_overlaps
from pipeline.reconstructor import rebuild_pdf


def _check_pdf_quality(pdf_path: str, label: str):
    """Check a PDF has actual drawable content."""
    doc   = fitz.open(pdf_path)
    page  = doc[0]
    draws = page.get_drawings()
    texts = [
        s.get("text","")
        for b in page.get_text("dict").get("blocks",[])
        if b.get("type")==0
        for l in b.get("lines",[])
        for s in l.get("spans",[])
        if s.get("text","").strip()
    ]
    doc.close()

    print(f"  [{label}]")
    print(f"    Drawing paths : {len(draws)}")
    print(f"    Text spans    : {len(texts)}")
    if texts:
        print(f"    Texts found   : {texts[:4]}")
    else:
        print(f"    ⚠ No text extracted (may be vector text)")

    if len(draws) == 0:
        print(f"  ⚠ WARNING: No drawing paths — PDF may be blank!")
    else:
        print(f"  ✅ PDF has content")

    return len(draws), len(texts)


def test_1_engines():
    print("\n[TEST 1] DWG Engine Detection")
    engines = _detect_dwg_engine()
    if engines:
        for name, path in engines.items():
            print(f"  ✅ {name} → {path}")
    else:
        print("  ⚠ No DWG engines (DXF/PDF work fine)")
    print("✅ Engine test passed")
    return engines


def test_2_dxf_conversion(engines):
    print("\n[TEST 2] DXF → PDF Conversion Quality")
    dxf = generate_sample_dxf("/tmp/t2.dxf")
    pdf = convert_to_pdf(dxf, "/tmp/t2.pdf")

    assert os.path.exists(pdf), "PDF not created"
    assert os.path.getsize(pdf) > 500, "PDF too small"

    print(f"  Size: {os.path.getsize(pdf)//1024}KB")
    draws, texts = _check_pdf_quality(pdf, "DXF→PDF")

    assert draws > 0, "PDF has no drawing content — conversion failed!"
    print("✅ DXF conversion passed")
    return pdf


def test_3_parser():
    print("\n[TEST 3] Parser (real-text PDF)")
    pdf    = generate_sample_pdf_with_text("/tmp/t3.pdf")
    parsed = parse_pdf(pdf)

    assert parsed.page_count >= 1
    print(f"  Pages    : {parsed.page_count}")
    print(f"  Text     : {len(parsed.all_text_blocks)}")
    print(f"  Drawings : {len(parsed.all_drawing_elements)}")

    if parsed.all_text_blocks:
        for tb in parsed.all_text_blocks[:3]:
            print(f"    '{tb.text}' size={tb.font_size:.1f}")

    print("✅ Parser passed")
    return parsed


def test_4_detector(parsed=None):
    print("\n[TEST 4] Overlap Detection + Zone Analysis")
    if not parsed:
        pdf    = generate_sample_pdf_with_text("/tmp/t4.pdf")
        parsed = parse_pdf(pdf)

    result = detect_overlaps(parsed)
    print(f"  Overlaps : {result.has_overlaps}")
    print(f"  Conflicts: {result.total_conflicts}")
    print(f"  Summary  : {result.summary}")

    for z in result.page_zones:
        print(
            f"  Page {z.page_no}: "
            f"filled={len(z.filled_zones)} "
            f"free={len(z.free_zones)}"
        )

    if result.conflicts:
        for c in result.conflicts[:3]:
            print(
                f"    '{c.text}' "
                f"type={c.conflict_type} "
                f"overlap={c.max_overlap_ratio:.0%}"
            )

    print("✅ Detector passed")
    return result


def test_5_full_pipeline():
    print("\n[TEST 5] Full Pipeline (end-to-end)")
    pdf    = generate_sample_pdf_with_text("/tmp/t5.pdf")
    parsed = parse_pdf(pdf)
    result = detect_overlaps(parsed)

    print(f"  Conflicts: {result.total_conflicts}")

    if result.has_overlaps:
        from agent.tools     import initialize_session, get_committed_positions
        from agent.cad_agent import _fallback_fix, _zones_to_session_dicts

        page_dims      = [(p.width,p.height) for p in parsed.pages]
        drawing_bboxes = [d.bbox for p in parsed.pages for d in p.drawing_elements]
        text_bboxes    = [t.bbox for p in parsed.pages for t in p.text_blocks]
        filled, free   = _zones_to_session_dicts(result.page_zones)

        initialize_session(
            page_dimensions=page_dims,
            text_bboxes=text_bboxes,
            drawing_bboxes=drawing_bboxes,
            filled_zones=filled,
            free_zones=free,
        )

        for conflict in result.conflicts:
            _fallback_fix(conflict, free)

        fixes = get_committed_positions()
        print(f"  Fixes: {len(fixes)}")

        out = "/tmp/t5_corrected.pdf"
        rebuild_pdf(pdf, fixes, out)
        assert os.path.exists(out)

        draws, texts = _check_pdf_quality(out, "Corrected PDF")
        assert draws > 0, "Corrected PDF is blank!"
        print(f"  Output: {os.path.getsize(out)//1024}KB")

    print("✅ Full pipeline passed")


def test_6_pdf_passthrough():
    print("\n[TEST 6] PDF Passthrough")
    pdf  = generate_sample_pdf_with_text("/tmp/t6_src.pdf")
    out  = convert_to_pdf(pdf, "/tmp/t6_out.pdf")
    assert out == pdf, "PDF passthrough should return same path"
    print("✅ Passthrough passed")


if __name__ == "__main__":
    print("=" * 55)
    print("  CAD Overlap Fixer — Tests v3.0")
    print("=" * 55)

    errors = []
    engines = {}

    for name, fn in [
        ("Engines",       lambda: test_1_engines()),
        ("DXF Convert",   lambda: test_2_dxf_conversion(engines)),
        ("Parser",        lambda: test_3_parser()),
        ("Detector",      lambda: test_4_detector()),
        ("Full Pipeline", lambda: test_5_full_pipeline()),
        ("PDF Passthrough",lambda: test_6_pdf_passthrough()),
    ]:
        try:
            result = fn()
            if name == "Engines" and result:
                engines.update(result)
        except Exception as e:
            import traceback
            print(f"❌ {name} FAILED: {e}")
            traceback.print_exc()
            errors.append(name)

    print()
    print("=" * 55)
    if errors:
        print(f"  ❌ {len(errors)} failed: {', '.join(errors)}")
    else:
        print("  ✅ All 6 tests passed!")
    print("=" * 55)
