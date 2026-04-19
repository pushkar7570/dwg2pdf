# scripts/generate_test_dxf.py
"""
Generates a sample DXF file that simulates a simple building floor plan
with intentional text overlaps for testing the pipeline.
"""

import ezdxf
import os


def generate_sample_dxf(output_path: str = "tests/sample_files/sample.dxf"):
    """
    Creates a simple floor plan DXF with:
    - Outer walls (rectangle)
    - Internal room dividers
    - Door openings
    - Text labels intentionally placed on top of lines (overlapping)
    - Text labels in correct positions (non-overlapping)
    """
    doc = ezdxf.new(dxfversion="R2010")
    msp = doc.modelspace()

    # ── Outer Walls ──────────────────────────────────────────────────────────
    # Main building outline 0,0 to 200,150
    msp.add_line((0, 0), (200, 0))    # bottom wall
    msp.add_line((200, 0), (200, 150))  # right wall
    msp.add_line((200, 150), (0, 150))  # top wall
    msp.add_line((0, 150), (0, 0))    # left wall

    # ── Internal Walls ───────────────────────────────────────────────────────
    # Vertical divider at x=100 (splits building into left/right)
    msp.add_line((100, 0), (100, 60))
    msp.add_line((100, 80), (100, 150))   # Gap for door

    # Horizontal divider at y=75 in right half
    msp.add_line((100, 75), (200, 75))

    # Another room divider
    msp.add_line((0, 80), (60, 80))
    msp.add_line((80, 80), (100, 80))

    # ── Door Symbols ─────────────────────────────────────────────────────────
    # Door arc at vertical wall gap (100, 60 to 100, 80)
    msp.add_arc(center=(100, 80), radius=20, start_angle=270, end_angle=360)

    # Door on left wall
    msp.add_arc(center=(0, 40), radius=15, start_angle=0, end_angle=90)

    # ── Window Lines ─────────────────────────────────────────────────────────
    # Windows on top wall
    msp.add_line((30, 150), (60, 150), dxfattribs={"lineweight": 30})
    msp.add_line((130, 150), (170, 150), dxfattribs={"lineweight": 30})

    # ── Dimension Lines ──────────────────────────────────────────────────────
    # Horizontal dimension line at bottom
    msp.add_line((0, -10), (200, -10))
    msp.add_line((0, -8), (0, -12))   # tick
    msp.add_line((200, -8), (200, -12))  # tick

    # Vertical dimension line on right
    msp.add_line((210, 0), (210, 150))
    msp.add_line((208, 0), (212, 0))   # tick
    msp.add_line((208, 150), (212, 150))  # tick

    # ── TEXT LABELS ──────────────────────────────────────────────────────────
    # These are intentionally placed ON the lines to create overlaps

    # ❌ OVERLAPPING: Room label on top of internal wall line
    msp.add_text(
        "Living Room",
        dxfattribs={"height": 5, "insert": (95, 75)}  # ON the vertical wall at x=100
    )

    # ❌ OVERLAPPING: Dimension text ON the dimension line
    msp.add_text(
        "200mm",
        dxfattribs={"height": 4, "insert": (95, -11)}  # ON dimension line at y=-10
    )

    # ❌ OVERLAPPING: Room number on internal wall
    msp.add_text(
        "RM-101",
        dxfattribs={"height": 4, "insert": (98, 38)}  # Very close to x=100 wall
    )

    # ❌ OVERLAPPING: Label crossing horizontal wall
    msp.add_text(
        "Bedroom",
        dxfattribs={"height": 5, "insert": (30, 78)}  # ON horizontal wall at y=80
    )

    # ✅ CORRECT: Title block text (well clear of drawings)
    msp.add_text(
        "FLOOR PLAN - GROUND LEVEL",
        dxfattribs={"height": 6, "insert": (30, -30)}
    )

    # ✅ CORRECT: Room label in open space
    msp.add_text(
        "Kitchen",
        dxfattribs={"height": 5, "insert": (140, 110)}  # Center of top-right room
    )

    # ✅ CORRECT: Scale notation
    msp.add_text(
        "Scale 1:100",
        dxfattribs={"height": 4, "insert": (140, -30)}
    )

    # ❌ OVERLAPPING: Vertical dimension text on line
    msp.add_text(
        "150mm",
        dxfattribs={"height": 4, "insert": (209, 70)}  # ON vertical dimension line
    )

    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.saveas(output_path)
    print(f"✅ Sample DXF created: {output_path}")
    print(f"   Contains 4 intentional overlaps for testing")
    return output_path


if __name__ == "__main__":
    generate_sample_dxf()