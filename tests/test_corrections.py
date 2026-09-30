import json

from meridian.db import connect, init_schema
from meridian.ingest_bom import ingest_bom
from meridian.ingest_drawings import ingest_drawings
from meridian.mapping import link_drawings_to_bom
from meridian.paths import BOM_CSV, DRAWINGS_DIR, MANIFEST_JSON
from meridian.tools import get_part_facts, propose_correction, review_correction


def _ready(db_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_bom(conn, BOM_CSV)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    link_drawings_to_bom(conn)
    return conn


def test_proposal_stores_checks_and_the_original_evidence(db_path):
    conn = _ready(db_path)
    bom_id = conn.execute("SELECT id FROM bom_rows WHERE name = 'Recoater head'").fetchone()["id"]
    proposal = propose_correction(
        conn,
        entity_type="bom_row",
        entity_id=str(bom_id),
        field="material",
        new_value="EN-AW 5005",
        reason="title-block alloy",
    )
    stored = conn.execute(
        "SELECT checks, target_fact_ref, chat_turn_id FROM corrections WHERE id = ?",
        (proposal["id"],),
    ).fetchone()
    checks = json.loads(stored["checks"])
    assert stored["target_fact_ref"] == f"bom_row:{bom_id}:material"
    assert stored["chat_turn_id"] is None
    assert checks["part_exists"] is True
    assert checks["field_correctable"] is True
    assert checks["bom_value"] == "Aluminium"
    assert checks["csv_line"] == 28
    assert checks["file_line"] == 33
    assert checks["drawing"]["drawing_id"]
    assert "EN-AW 5005" in checks["drawing"]["value"]
    assert checks["agreement"] == "drawing"
    assert proposal["checks"]["file_line"] == 33


def test_recoater_head_material_before_accept(db_path):
    conn = _ready(db_path)
    bom_id = conn.execute("SELECT id FROM bom_rows WHERE name = 'Recoater head'").fetchone()["id"]
    result = get_part_facts(conn, bom_row_id=bom_id)
    assert result["current_value"] == "Aluminium"
    assert result["current_source"]["source"] == "BOM"
    assert result["conflict"] is True


def test_accepted_correction_resolves_conflict_and_keeps_the_original_cell(db_path):
    conn = _ready(db_path)
    bom_id = conn.execute("SELECT id FROM bom_rows WHERE name = 'Recoater head'").fetchone()["id"]
    proposal = propose_correction(
        conn,
        entity_type="bom_row",
        entity_id=str(bom_id),
        field="material",
        new_value="EN-AW 5005",
        reason="title-block alloy",
    )
    review_correction(conn, proposal["id"], "accepted", reviewer="test")
    result = get_part_facts(conn, bom_row_id=bom_id)
    assert result["current_value"] == "EN-AW 5005"
    assert result["current_source"]["source"] == "accepted_correction"
    assert result["current_source"]["correction_id"] == proposal["id"]
    assert result["current_source"]["date"]
    assert result["original_value"] == "Aluminium"
    assert result["original_citation"]["value"] == "Aluminium"
    assert result["original_citation"]["kind"] == "bom_row"
    assert result["original_citation"]["csv_line"]
    assert result["conflict"] is False
    assert result["conflict_history"]
    assert "Aluminium" in result["conflict_history"][0]
    assert f"correction #{proposal['id']}" in result["conflict_history"][0]
    raw = conn.execute("SELECT material_raw FROM bom_rows WHERE id = ?", (bom_id,)).fetchone()
    assert raw["material_raw"] == "Aluminium"


def test_rejected_correction_matches_the_uncorrected_facts(db_path):
    conn = _ready(db_path)
    bom_id = conn.execute("SELECT id FROM bom_rows WHERE name = 'Recoater head'").fetchone()["id"]
    before = get_part_facts(conn, bom_row_id=bom_id)
    proposal = propose_correction(
        conn,
        entity_type="bom_row",
        entity_id=str(bom_id),
        field="material",
        new_value="EN-AW 5005",
        reason="title-block alloy",
    )
    review_correction(conn, proposal["id"], "rejected", reviewer="test")
    after = get_part_facts(conn, bom_row_id=bom_id)
    for key in ("current_value", "original_value", "conflict", "conflict_history"):
        assert after[key] == before[key]
    assert after["current_source"] == before["current_source"]


def test_rejected_correction_does_not_change_current_value(db_path):
    conn = _ready(db_path)
    bom_id = conn.execute("SELECT id FROM bom_rows WHERE name = 'Recoater head'").fetchone()["id"]
    before = get_part_facts(conn, bom_row_id=bom_id)
    proposal = propose_correction(
        conn,
        entity_type="bom_row",
        entity_id=str(bom_id),
        field="material",
        new_value="EN-AW 5005",
        reason="title-block alloy",
    )
    review_correction(conn, proposal["id"], "rejected", reviewer="test")
    after = get_part_facts(conn, bom_row_id=bom_id)
    assert after["current"]["material"] == before["current"]["material"]
    raw = conn.execute("SELECT material_raw FROM bom_rows WHERE id = ?", (bom_id,)).fetchone()
    assert raw["material_raw"] == "Aluminium"


def test_accepted_correction_changes_current_value_and_keeps_original(db_path):
    conn = _ready(db_path)
    bom_id = conn.execute("SELECT id FROM bom_rows WHERE name = 'Recoater head'").fetchone()["id"]
    proposal = propose_correction(
        conn,
        entity_type="bom_row",
        entity_id=str(bom_id),
        field="material",
        new_value="EN-AW 5005",
        reason="D-013 title-block 3.3315 (EN-AW 5005)",
        check_notes="Drawing D-013 weight note lists EN-AW 5005",
    )
    review_correction(conn, proposal["id"], "accepted", reviewer="test")
    after = get_part_facts(conn, bom_row_id=bom_id)
    assert after["current"]["material"] == "EN-AW 5005"
    assert after["bom"]["material"] == "Aluminium"
    assert any(
        cite["kind"] == "correction" and cite["value"] == "EN-AW 5005"
        for cite in after["citations"]
    )
    raw = conn.execute("SELECT material_raw FROM bom_rows WHERE id = ?", (bom_id,)).fetchone()
    assert raw["material_raw"] == "Aluminium"


def test_propose_uses_part_name_when_entity_id_is_the_csv_line(db_path):
    conn = _ready(db_path)
    head = conn.execute(
        "SELECT id, source_row FROM bom_rows WHERE name = 'Recoater head'"
    ).fetchone()
    clamp = conn.execute("SELECT id FROM bom_rows WHERE name = 'Recoater Clamp'").fetchone()
    assert head["source_row"] == clamp["id"]
    proposal = propose_correction(
        conn,
        entity_type="bom_row",
        entity_id=str(head["source_row"]),
        part="Recoater head",
        field="material",
        new_value="EN-AW 5005",
        reason="title-block alloy",
    )
    stored = conn.execute(
        "SELECT entity_id, checks FROM corrections WHERE id = ?", (proposal["id"],)
    ).fetchone()
    assert stored["entity_id"] == str(head["id"])
    assert json.loads(stored["checks"])["part_name"] == "Recoater head"
    review_correction(conn, proposal["id"], "accepted", reviewer="test")
    head_facts = get_part_facts(conn, bom_row_id=head["id"])
    clamp_facts = get_part_facts(conn, bom_row_id=clamp["id"])
    assert head_facts["current_value"] == "EN-AW 5005"
    assert clamp_facts["current_value"] != "EN-AW 5005"
