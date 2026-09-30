from meridian.db import connect, init_schema
from meridian.ingest_drawings import extract_title_block, ingest_drawings
from meridian.paths import DRAWINGS_DIR, MANIFEST_JSON


def test_title_block_parser_reads_weight_then_title():
    text = """
WEIGHT [g]:	AISI 316 Stainless Steel Sheet (SS) 	7822.5
Build Plate
UNLESS OTHERWISE SPECIFIED:
DIMENSIONS ARE IN MILLIMETERS
"""
    block = extract_title_block(text)
    assert block["title"] == "Build Plate"
    assert "AISI 316" in (block["material"] or "")
    assert block["weight_g"] == 7822.5


def test_title_block_parser_reads_recoater_plate():
    text = """
Alu.
Justeringsplade til recoater.
WEIGHT [g]:	3.3315 (EN-AW 5005) 	87.5
Recoater Plate
UNLESS OTHERWISE SPECIFIED:
"""
    block = extract_title_block(text)
    assert block["title"] == "Recoater Plate"
    assert block["weight_g"] == 87.5
    assert block["material"] == "3.3315 (EN-AW 5005)"


def test_ingest_all_thirty_drawings(db_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    count = conn.execute("SELECT COUNT(*) FROM drawings").fetchone()[0]
    assert count == 30


def test_clean_drawings_have_expected_titles(db_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    titles = {
        row["id"]: row["title_extracted"]
        for row in conn.execute(
            "SELECT id, title_extracted FROM drawings WHERE id IN "
            "('D-013','D-015','D-016','D-026')"
        )
    }
    assert titles["D-013"] == "Recoater Plate"
    material = conn.execute(
        "SELECT material_extracted FROM drawings WHERE id = 'D-013'"
    ).fetchone()["material_extracted"]
    assert material == "3.3315 (EN-AW 5005)"
    assert titles["D-015"] == "Silikone Wiper"
    assert titles["D-016"] == "Galvo Bundplade"
    assert titles["D-026"] == "Build Plate"
    for drawing_id in titles:
        row = conn.execute(
            "SELECT title_confidence, extract_method, distribution FROM drawings WHERE id = ?",
            (drawing_id,),
        ).fetchone()
        assert row["distribution"] == "clean"
        assert row["title_confidence"] == "high"
        assert row["extract_method"] == "pdf_text"


def test_degraded_drawings_are_flagged_not_guessed(db_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    rows = conn.execute(
        "SELECT id, title_confidence, distribution FROM drawings "
        "WHERE distribution = 'degraded-only' ORDER BY id"
    ).fetchall()
    ids = [row["id"] for row in rows]
    assert ids == [
        "D-001",
        "D-003",
        "D-009",
        "D-011",
        "D-020",
        "D-022",
        "D-023",
        "D-030",
    ]
    for row in rows:
        assert row["title_confidence"] in {"low", "illegible", "missing"}


def test_degraded_reviews_write_readable_title_blocks(db_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    titles = {
        row["id"]: row
        for row in conn.execute(
            """
            SELECT id, title_extracted, material_extracted, title_confidence, extract_method
            FROM drawings
            WHERE distribution = 'degraded-only'
            """
        )
    }
    assert titles["D-001"]["title_extracted"] == "Door - x2"
    assert titles["D-001"]["material_extracted"] == "Aluminium"
    assert titles["D-003"]["title_extracted"] == "20mm thick aluminium plate"
    assert titles["D-009"]["title_extracted"] == "Powder canister"
    assert titles["D-011"]["title_extracted"] == "Recoater Arm"
    assert titles["D-011"]["material_extracted"] == "7075-T6"
    assert titles["D-020"]["title_extracted"] == "Seal Ring"
    assert titles["D-022"]["title_extracted"] == "Inlet Box"
    assert titles["D-023"]["title_extracted"] == "Bygge Cylinder"
    assert titles["D-030"]["title_extracted"] == "Z-axis Motor plate"
    for row in titles.values():
        assert row["extract_method"] == "vision_review"
        assert row["title_confidence"] == "low"
