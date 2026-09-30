from meridian.db import connect, init_schema
from meridian.ingest_bom import ingest_bom
from meridian.ingest_drawings import ingest_drawings
from meridian.mapping import apply_mapping_overrides, link_drawings_to_bom
from meridian.paths import BOM_CSV, DRAWINGS_DIR, MANIFEST_JSON


def _linked(db_path, overrides=None):
    conn = connect(db_path)
    init_schema(conn)
    ingest_bom(conn, BOM_CSV)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    link_drawings_to_bom(conn)
    if overrides:
        apply_mapping_overrides(conn, overrides)
    return conn


def test_datasheet_matches_four_recoater_drawings(db_path):
    conn = _linked(db_path)
    rows = conn.execute(
        """
        SELECT drawing_id, method, status
        FROM drawing_bom_links
        WHERE method = 'datasheet'
        ORDER BY drawing_id
        """
    ).fetchall()
    drawing_ids = {row["drawing_id"] for row in rows}
    assert drawing_ids == {"D-011", "D-012", "D-013", "D-015"}
    assert all(row["status"] in {"confirmed", "conflict"} for row in rows)


def test_d013_datasheet_is_a_conflict(db_path):
    conn = _linked(db_path)
    names = {
        row["name"]
        for row in conn.execute(
            """
            SELECT b.name
            FROM drawing_bom_links l
            JOIN bom_rows b ON b.id = l.bom_row_id
            WHERE l.drawing_id = 'D-013' AND l.method = 'datasheet'
            """
        )
    }
    assert names == {"Recoater head", "Recoater stage plate"}
    statuses = {
        row[0]
        for row in conn.execute(
            "SELECT status FROM drawing_bom_links WHERE drawing_id = 'D-013' AND method = 'datasheet'"
        )
    }
    assert statuses == {"conflict"}


def test_d026_title_matches_build_plate(db_path):
    conn = _linked(db_path)
    row = conn.execute(
        """
        SELECT b.name, l.method, l.status, l.score
        FROM drawing_bom_links l
        JOIN bom_rows b ON b.id = l.bom_row_id
        WHERE l.drawing_id = 'D-026'
        ORDER BY l.score DESC
        """
    ).fetchone()
    assert row["name"] == "Build Plate"
    assert row["method"] == "title"
    assert row["status"] == "confirmed"
    assert row["score"] >= 0.95


def test_mapping_override_can_confirm_a_candidate(db_path, tmp_path):
    conn = _linked(db_path)
    override_path = tmp_path / "overrides.json"
    override_path.write_text(
        '{"D-014": {"bom_name": "Recoater stage plate", "status": "confirmed"}}',
        encoding="utf-8",
    )
    apply_mapping_overrides(conn, override_path)
    row = conn.execute(
        """
        SELECT b.name, l.method, l.status
        FROM drawing_bom_links l
        JOIN bom_rows b ON b.id = l.bom_row_id
        WHERE l.drawing_id = 'D-014' AND l.method = 'manual'
        """
    ).fetchone()
    assert row["name"] == "Recoater stage plate"
    assert row["status"] == "confirmed"
