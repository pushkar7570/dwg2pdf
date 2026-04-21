# scripts/generate_test_dxf.py

import os
import ezdxf
import fitz


def generate_sample_dxf(
    output_path: str = "tests/sample_files/sample.dxf"
) -> str:
    """
    Simple floor plan DXF with 4 intentional text overlaps.
    Tests conversion pipeline quality.
    """
    doc = ezdxf.new(dxfversion="R2010")
    msp = doc.modelspace()

    # Outer walls
    msp.add_lwpolyline(
        [(0,0),(200,0),(200,150),(0,150),(0,0)],
        close=True,
        dxfattribs={"layer": "WALLS"}
    )

    # Internal walls
    msp.add_line((100,0),   (100,60),  dxfattribs={"layer":"WALLS"})
    msp.add_line((100,80),  (100,150), dxfattribs={"layer":"WALLS"})
    msp.add_line((100,75),  (200,75),  dxfattribs={"layer":"WALLS"})
    msp.add_line((0,80),    (60,80),   dxfattribs={"layer":"WALLS"})
    msp.add_line((80,80),   (100,80),  dxfattribs={"layer":"WALLS"})

    # Doors
    msp.add_arc(center=(100,80),radius=20,start_angle=270,end_angle=360,
                dxfattribs={"layer":"DOORS"})
    msp.add_arc(center=(0,40), radius=15,start_angle=0,  end_angle=90,
                dxfattribs={"layer":"DOORS"})

    # Windows
    msp.add_line((30,150),(60,150),  dxfattribs={"layer":"WINDOWS"})
    msp.add_line((130,150),(170,150),dxfattribs={"layer":"WINDOWS"})

    # Dimension lines
    msp.add_line((0,-10),  (200,-10), dxfattribs={"layer":"DIMS"})
    msp.add_line((0,-8),   (0,-12),   dxfattribs={"layer":"DIMS"})
    msp.add_line((200,-8), (200,-12), dxfattribs={"layer":"DIMS"})
    msp.add_line((210,0),  (210,150), dxfattribs={"layer":"DIMS"})

    # ── Text: 4 OVERLAPPING (on lines) ───────────────────────────
    msp.add_text(
        "Living Room",
        dxfattribs={"height":5,"insert":(95,75),"layer":"TEXT"}
    )
    msp.add_text(
        "200mm",
        dxfattribs={"height":4,"insert":(95,-11),"layer":"DIMS"}
    )
    msp.add_text(
        "RM-101",
        dxfattribs={"height":4,"insert":(98,38),"layer":"TEXT"}
    )
    msp.add_text(
        "Bedroom",
        dxfattribs={"height":5,"insert":(30,78),"layer":"TEXT"}
    )

    # ── Text: CORRECTLY PLACED (no overlap) ──────────────────────
    msp.add_text(
        "FLOOR PLAN",
        dxfattribs={"height":7,"insert":(30,-30),"layer":"TITLE"}
    )
    msp.add_text(
        "Kitchen",
        dxfattribs={"height":5,"insert":(140,110),"layer":"TEXT"}
    )
    msp.add_text(
        "Scale 1:100",
        dxfattribs={"height":4,"insert":(140,-30),"layer":"TITLE"}
    )
    msp.add_text(
        "150mm",
        dxfattribs={"height":4,"insert":(209,70),"layer":"DIMS"}
    )

    os.makedirs(
        os.path.dirname(output_path) if os.path.dirname(output_path) else ".",
        exist_ok=True
    )
    doc.saveas(output_path)
    print(f"✅ Sample DXF: {output_path}")
    print(f"   4 intentional overlaps for testing")
    return output_path


def generate_sample_pdf_with_text(
    output_path: str = "tests/sample_files/sample_with_text.pdf"
) -> str:
    """
    Direct PDF with real text objects + drawing elements.
    Used for testing overlap detection without DXF conversion.
    """
    os.makedirs(
        os.path.dirname(output_path) if os.path.dirname(output_path) else ".",
        exist_ok=True
    )

    doc  = fitz.open()
    page = doc.new_page(width=595, height=420)

    # Outer boundary
    page.draw_rect(
        fitz.Rect(50,50,450,320),
        color=(0,0,0), width=2
    )
    # Internal walls
    page.draw_line(fitz.Point(250,50),  fitz.Point(250,200), color=(0,0,0), width=2)
    page.draw_line(fitz.Point(250,230), fitz.Point(250,320), color=(0,0,0), width=2)
    page.draw_line(fitz.Point(250,185), fitz.Point(450,185), color=(0,0,0), width=2)
    page.draw_line(fitz.Point(50,200),  fitz.Point(160,200), color=(0,0,0), width=2)
    page.draw_line(fitz.Point(190,200), fitz.Point(250,200), color=(0,0,0), width=2)
    # Dimension lines
    page.draw_line(fitz.Point(50,350),  fitz.Point(450,350), color=(0,0,0), width=1)
    page.draw_line(fitz.Point(470,50),  fitz.Point(470,320), color=(0,0,0), width=1)

    # ── Overlapping text (on lines) ───────────────────────────────
    page.insert_text(fitz.Point(240,185),"Living Room",fontsize=10,color=(1,0,0))
    page.insert_text(fitz.Point(220,352),"200mm",      fontsize=9, color=(1,0,0))
    page.insert_text(fitz.Point(80,202), "Bedroom",    fontsize=10,color=(1,0,0))
    page.insert_text(fitz.Point(245,120),"RM-101",     fontsize=8, color=(1,0,0))

    # ── Correct text ──────────────────────────────────────────────
    page.insert_text(fitz.Point(120,270),"Kitchen",    fontsize=10,color=(1,0,0))
    page.insert_text(fitz.Point(320,100),"Bathroom",   fontsize=10,color=(1,0,0))
    page.insert_text(fitz.Point(50,395), "FLOOR PLAN", fontsize=11,color=(0,0,0))
    page.insert_text(fitz.Point(350,395),"Scale 1:100",fontsize=9, color=(0,0,0))
    page.insert_text(fitz.Point(475,190),"270mm",      fontsize=9, color=(0,0,0))

    doc.save(output_path)
    doc.close()

    print(f"✅ Sample PDF (real text): {output_path}")
    return output_path


if __name__ == "__main__":
    os.makedirs("tests/sample_files", exist_ok=True)
    generate_sample_dxf()
    generate_sample_pdf_with_text()
